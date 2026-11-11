package com.severeclaw.downloader

import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaMetadataRetriever
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.drag
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.SeekParameters
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import coil.compose.AsyncImage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/** ffmpeg из состава приложения (тот же, которым пользуется yt-dlp). */
object Ff {
    private val bin: File get() = File(Engine.app.applicationInfo.nativeLibraryDir, "libffmpeg.so")

    fun start(args: List<String>): Process {
        val pk = File(Engine.app.noBackupFilesDir, "youtubedl-android/packages")
        val libs = listOf("ffmpeg", "python", "aria2c").joinToString(":") { File(pk, "$it/usr/lib").absolutePath }
        val pb = ProcessBuilder(listOf(bin.absolutePath) + args).redirectErrorStream(true)
        pb.environment()["LD_LIBRARY_PATH"] = libs
        pb.environment()["TMPDIR"] = Engine.app.cacheDir.absolutePath
        return pb.start()
    }

    /** Запуск с отслеживанием хода (секунды готового результата). Возвращает (код, хвост вывода). */
    fun run(args: List<String>, onProcess: (Process) -> Unit = {}, onTime: (Double) -> Unit = {}): Pair<Int, String> {
        val p = start(listOf("-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-nostats",
            "-progress", "pipe:1") + args)
        onProcess(p)
        val tail = ArrayDeque<String>()
        p.inputStream.bufferedReader().forEachLine { line ->
            val us = line.removePrefix("out_time_us=").takeIf { it != line }
                ?: line.removePrefix("out_time_ms=").takeIf { it != line }
            if (us != null) {
                us.toLongOrNull()?.let { onTime(it / 1_000_000.0) }
            } else if ('=' !in line || line.length > 40) {
                tail.addLast(line)
                if (tail.size > 6) tail.removeFirst()
            }
        }
        return p.waitFor() to tail.joinToString("\n")
    }
}

/** Обрезка: выбор команд ffmpeg и сохранение результата рядом с исходным файлом. */
object Trim {
    /** 83.4 с → «1'23» — как в программе для ПК, чтобы имена обрезок совпадали. */
    fun stamp(ms: Long): String {
        val s = ms / 1000
        return "${s / 60}'${(s % 60).toString().padStart(2, '0')}"
    }

    fun clock(ms: Long): String {
        val t = ms / 100
        val m = t / 600
        return "$m:${(t / 10 % 60).toString().padStart(2, '0')}.${t % 10}"
    }

    fun isAudio(f: File) = f.extension.lowercase() in AUDIO_EXT

    /** Расширение результата. sound: "on" | "off" | "only". */
    fun outExt(src: File, exact: Boolean, sound: String): String {
        val ext = src.extension.lowercase()
        return when {
            sound == "only" && !isAudio(src) -> "mp3"
            isAudio(src) -> ext
            exact -> "mp4"
            else -> ext
        }
    }

    /** Варианты команды по убыванию желательности: если первый не сработал — пробуем следующий. */
    fun commands(src: File, out: File, startMs: Long, endMs: Long, exact: Boolean, sound: String): List<List<String>> {
        val head = listOf("-ss", "%.3f".format(java.util.Locale.ROOT, startMs / 1000.0), "-i", src.absolutePath,
            "-t", "%.3f".format(java.util.Locale.ROOT, (endMs - startMs) / 1000.0))
        val o = out.absolutePath
        if (isAudio(src)) {
            val codec = if (!exact) listOf("-c:a", "copy") else when (src.extension.lowercase()) {
                "mp3" -> listOf("-c:a", "libmp3lame", "-q:a", "2")
                "m4a", "aac" -> listOf("-c:a", "aac", "-b:a", "192k")
                "opus", "ogg" -> listOf("-c:a", "libopus", "-b:a", "160k")
                "flac" -> listOf("-c:a", "flac")
                else -> listOf("-c:a", "pcm_s16le")
            }
            return listOf(
                // с обложкой трека
                head + listOf("-map", "0:a:0", "-map", "0:v?") + codec +
                    listOf("-c:v", "copy", "-disposition:v", "attached_pic", "-map_metadata", "0", o),
                head + listOf("-map", "0:a:0") + codec + listOf("-map_metadata", "0", o),
            )
        }
        if (sound == "only") {
            return listOf(head + listOf("-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-q:a", "2", o))
        }
        val audio = if (sound == "off") listOf("-an") else listOf("-map", "0:a:0?")
        if (!exact) {
            return listOf(head + listOf("-map", "0:v:0") + audio +
                listOf("-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", o))
        }
        val acodec = if (sound == "off") emptyList() else listOf("-c:a", "aac", "-b:a", "192k")
        val tailArgs = acodec + listOf("-movflags", "+faststart", o)
        return listOf(
            head + listOf("-map", "0:v:0") + audio + listOf("-c:v", "libx264", "-preset", "veryfast",
                "-crf", "20", "-pix_fmt", "yuv420p") + tailArgs,
            head + listOf("-map", "0:v:0") + audio + listOf("-c:v", "mpeg4", "-q:v", "3") + tailArgs,
        )
    }

    class Job { @Volatile var process: Process? = null; @Volatile var cancelled = false
        fun cancel() { cancelled = true; runCatching { process?.destroy() } } }

    /** Обрезает и кладёт результат в папку исходного файла. Возвращает файл или текст ошибки. */
    fun export(rec: Rec, startMs: Long, endMs: Long, exact: Boolean, sound: String, job: Job = Job(),
               onProgress: (Float) -> Unit = {}): Pair<File?, String?> {
        val src = File(rec.file)
        val ext = outExt(src, exact, sound)
        val work = File(Engine.app.cacheDir, "trim").apply { deleteRecursively(); mkdirs() }
        val tmp = File(work, "out.$ext")
        val dur = (endMs - startMs) / 1000.0
        var err = ""
        var ok = false
        for (cmd in commands(src, tmp, startMs, endMs, exact, sound)) {
            tmp.delete()
            val (code, tail) = Ff.run(cmd, { job.process = it }) { t ->
                onProgress((t / dur).toFloat().coerceIn(0f, 1f))
            }
            if (job.cancelled) { tmp.delete(); return null to null }
            if (code == 0 && tmp.length() > 0) { ok = true; break }
            err = tail
            Engine.log("ffmpeg (обрезка): код $code: ${tail.takeLast(300)}")
        }
        if (!ok) return null to ("Не получилось обрезать: " + err.lines().lastOrNull().orEmpty().take(140))
        val dir = src.parentFile?.takeIf { it.canWrite() } ?: Engine.downloadDir.apply { mkdirs() }
        val base = "${src.nameWithoutExtension.substringBefore(" (обрезка ")} (обрезка ${stamp(startMs)}–${stamp(endMs)})"
        var dst = File(dir, "$base.$ext")
        var n = 2
        while (dst.exists()) { dst = File(dir, "$base ($n).$ext"); n++ }
        try {
            if (!tmp.renameTo(dst)) { tmp.copyTo(dst); tmp.delete() }
        } catch (e: Throwable) {
            return null to "Не удалось сохранить: ${e.message}"
        }
        MediaScannerConnection.scanFile(Engine.app, arrayOf(dst.absolutePath), null, null)
        val opts = if (ext == "mp3" && !isAudio(src)) Opts("audio", "best", "mp3") else rec.opts
        Engine.addToHistory(Rec(url = rec.url, title = "${rec.title.substringBefore(" (обрезка ")} " +
                "(обрезка ${stamp(startMs)}–${stamp(endMs)})", file = dst.absolutePath, thumb = rec.thumb,
            date = System.currentTimeMillis(), opts = opts, duration = ((endMs - startMs) / 1000).toInt()))
        return dst to null
    }

    /** Картинка звуковой волны для музыки. */
    fun waveform(src: File): Bitmap? = runCatching {
        val out = File(Engine.app.cacheDir, "trim-wave.png").apply { delete() }
        val p = Ff.start(listOf("-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", src.absolutePath,
            "-filter_complex", "aformat=channel_layouts=mono,showwavespic=s=1600x160:colors=#E60012",
            "-frames:v", "1", out.absolutePath))
        p.inputStream.bufferedReader().readText()
        p.waitFor()
        BitmapFactory.decodeFile(out.absolutePath)
    }.getOrNull()

    fun durationMs(f: File): Long = runCatching {
        val r = MediaMetadataRetriever()
        try {
            r.setDataSource(f.absolutePath)
            r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLong() ?: 0L
        } finally {
            r.release()
        }
    }.getOrDefault(0L)
}

fun openEditor(ctx: Context, path: String) {
    if (Engine.history.value.none { it.file == path } || !File(path).isFile) {
        Engine.message.value = "Редактор работает только со скачанными здесь файлами"
        return
    }
    ctx.startActivity(Intent(ctx, EditorActivity::class.java).putExtra("path", path)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
}

/** ✂ Редактор: обрезка скачанного видео или музыки. */
class EditorActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val rec = Engine.history.value.firstOrNull { it.file == intent.getStringExtra("path") }
        if (rec == null || !File(rec.file).isFile) { finish(); return }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContent { EditorScreen(rec) { finish() } }
    }
}

@androidx.annotation.OptIn(UnstableApi::class)
@Composable
fun EditorScreen(rec: Rec, onClose: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val src = remember { File(rec.file) }
    val audio = remember { Trim.isAudio(src) }
    val player = remember {
        ExoPlayer.Builder(ctx).build().apply {
            setMediaItem(MediaItem.fromUri(Uri.fromFile(src)))
            prepare()
        }
    }
    DisposableEffect(Unit) { onDispose { player.release() } }

    var dur by remember { mutableStateOf(Trim.durationMs(src).coerceAtLeast(1L)) }
    var start by remember { mutableStateOf(0L) }
    var end by remember { mutableStateOf(dur) }
    var pos by remember { mutableStateOf(0L) }
    var playing by remember { mutableStateOf(false) }
    var dragging by remember { mutableStateOf(false) }
    var active by remember { mutableStateOf("start") }          // какую ручку двигают кнопки
    var viewA by remember { mutableStateOf(0L) }                // видимая часть ленты
    var viewB by remember { mutableStateOf(dur) }
    val zoomed = viewA > 0L || viewB < dur

    var exact by remember { mutableStateOf(false) }
    var sound by remember { mutableStateOf("on") }
    var progress by remember { mutableStateOf<Float?>(null) }
    var job by remember { mutableStateOf<Trim.Job?>(null) }
    var saved by remember { mutableStateOf<File?>(null) }
    var error by remember { mutableStateOf<String?>(null) }

    fun seek(ms: Long, fast: Boolean = false) {
        val t = ms.coerceIn(0L, dur)
        // пока палец ведёт — быстрый показ ближайшего ключевого кадра, отпустил — точный кадр
        player.setSeekParameters(if (fast) SeekParameters.CLOSEST_SYNC else SeekParameters.EXACT)
        player.seekTo(t)
        pos = t
    }

    fun setStart(ms: Long) { start = ms.coerceIn(0L, end - 100); active = "start"; saved = null }
    fun setEnd(ms: Long) { end = ms.coerceIn(start + 100, dur); active = "end"; saved = null }

    fun togglePlay() {
        if (player.isPlaying) {
            player.pause()
        } else {
            if (pos < start || pos >= end - 80) seek(start)
            player.play()
        }
    }

    // положение воспроизведения; на конце выделения — снова с начала (по кругу)
    LaunchedEffect(Unit) {
        while (isActive) {
            val d = player.duration
            if (d > 0 && abs(d - dur) > 50 && dur <= 1L) { dur = d; end = d; viewB = d }
            playing = player.isPlaying
            if (!dragging && playing) {
                pos = player.currentPosition
                if (pos >= end) seek(start)
            }
            delay(33)
        }
    }

    fun zoom() {
        if (zoomed) { viewA = 0L; viewB = dur; return }
        val pad = max((end - start) / 2, 1000L)
        viewA = (start - pad).coerceAtLeast(0L)
        viewB = (end + pad).coerceAtMost(dur)
    }

    fun save() {
        player.pause()
        error = null; saved = null; progress = 0f
        val j = Trim.Job(); job = j
        scope.launch {
            val (file, err) = withContext(Dispatchers.IO) {
                Trim.export(rec, start, end, exact, if (audio) "on" else sound, j) { progress = it }
            }
            progress = null; job = null
            saved = file; error = err
            if (file != null) Engine.message.value = "Обрезка сохранена — она в галерее"
        }
    }

    @Composable
    fun Preview(mod: Modifier) {
        Box(mod.background(Color.Black).clickable { togglePlay() }, contentAlignment = Alignment.Center) {
            if (audio) {
                Text("♪", color = P.RED, fontSize = 90.sp, fontWeight = FontWeight.Black)
                if (rec.thumb != null) {
                    AsyncImage(rec.thumb, null, Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
                }
            } else {
                val exo = player
                AndroidView(modifier = Modifier.fillMaxSize(), factory = { c ->
                    PlayerView(c).apply {
                        useController = false
                        resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT
                        setShutterBackgroundColor(android.graphics.Color.BLACK)
                        this.player = exo
                    }
                })
            }
            if (!playing) {
                Text("▶", color = P.WHITE, fontSize = 44.sp,
                    modifier = Modifier.background(Color(0x99000000)).padding(horizontal = 22.dp, vertical = 8.dp))
            }
        }
    }

    @Composable
    fun Controls(mod: Modifier) {
        Column(mod.padding(horizontal = 12.dp)) {
            Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(Trim.clock(pos), color = P.WHITE, fontWeight = FontWeight.Bold, fontSize = 15.sp)
                Spacer(Modifier.weight(1f))
                Text("выделено ${Trim.clock(end - start)} из ${Trim.clock(dur)}", color = P.MUTED, fontSize = 13.sp)
            }
            Timeline(src, audio, dur, viewA, viewB, start, end, pos,
                onStart = { ms, done -> dragging = !done; setStart(ms); seek(start, fast = !done) },
                onEnd = { ms, done -> dragging = !done; setEnd(ms); seek(end, fast = !done) },
                onScrub = { ms, done -> dragging = !done; seek(ms, fast = !done) })
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(Trim.clock(viewA), color = P.MUTED, fontSize = 11.sp)
                Spacer(Modifier.weight(1f))
                Text(if (zoomed) "🔍 приближено" else "", color = P.MUTED, fontSize = 11.sp)
                Spacer(Modifier.weight(1f))
                Text(Trim.clock(viewB), color = P.MUTED, fontSize = 11.sp)
            }

            // начало и конец — по месту, где сейчас стоит воспроизведение
            Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                PButton("[ Начало здесь", Modifier.weight(1f), primary = false, height = 42.dp) {
                    if (pos < end - 100) setStart(pos)
                }
                PButton(if (playing) "⏸" else "▶", height = 42.dp) { togglePlay() }
                PButton("Конец здесь ]", Modifier.weight(1f), primary = false, height = 42.dp) {
                    if (pos > start + 100) setEnd(pos)
                }
            }

            // точная подстройка выбранной ручки
            Row(Modifier.fillMaxWidth().padding(top = 10.dp).horizontalScroll(rememberScrollState()),
                verticalAlignment = Alignment.CenterVertically) {
                PChip("НАЧАЛО ${Trim.clock(start)}", active == "start") { active = "start"; seek(start) }
                PChip("КОНЕЦ ${Trim.clock(end)}", active == "end") { active = "end"; seek(end) }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                for ((label, step) in listOf("−1 с" to -1000L, "−0.1" to -100L, "+0.1" to 100L, "+1 с" to 1000L)) {
                    PButton(label, Modifier.weight(1f), primary = false, height = 38.dp) {
                        if (active == "start") { setStart(start + step); seek(start) }
                        else { setEnd(end + step); seek(end) }
                    }
                }
                PButton(if (zoomed) "↔" else "🔍", height = 38.dp) { zoom() }
            }

            // сохранение
            PLabel("Как сохранить")
            Row(Modifier.horizontalScroll(rememberScrollState())) {
                PChip("⚡ Быстро", !exact) { exact = false; saved = null }
                PChip("🎯 Точно", exact) { exact = true; saved = null }
                if (!audio) {
                    PChip("Со звуком", sound == "on") { sound = "on"; saved = null }
                    PChip("Без звука", sound == "off") { sound = "off"; saved = null }
                    PChip("Только звук MP3", sound == "only") { sound = "only"; saved = null }
                }
            }
            Text(if (exact) "Ровно по выделению. Видео перекодируется — это дольше."
                 else "Мгновенно и без потери качества. Начало может сдвинуться на 1–2 с назад, до ключевого кадра.",
                color = P.MUTED, fontSize = 12.sp)
            val p = progress
            if (p != null) {
                LinearProgressIndicator(progress = { p }, color = P.RED, trackColor = P.CARD2,
                    modifier = Modifier.fillMaxWidth().padding(top = 10.dp).height(6.dp))
                Row(Modifier.padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("Сохраняю… ${(p * 100).toInt()}%", color = P.WHITE, fontSize = 13.sp,
                        modifier = Modifier.weight(1f))
                    PButton("Отмена", primary = false, height = 36.dp) { job?.cancel() }
                }
            } else {
                PButton("✂ Сохранить обрезку", Modifier.fillMaxWidth().padding(top = 10.dp), height = 48.dp) { save() }
            }
            error?.let { Text(it, color = P.RED, fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp)) }
            saved?.let { f ->
                Text("✓ ${f.name}", color = P.GREEN, fontSize = 12.sp, maxLines = 2,
                    overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 6.dp))
                Row(Modifier.padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    PButton("Открыть", primary = false, height = 38.dp) { openFile(ctx, f.absolutePath) }
                    PButton("Поделиться", primary = false, height = 38.dp) { shareFile(ctx, f.absolutePath) }
                }
            }
            Spacer(Modifier.height(16.dp))
        }
    }

    Column(Modifier.fillMaxSize().background(P.BG)) {
        Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 8.dp, top = 8.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text("✂ РЕДАКТОР", color = P.RED, style = Heavy, fontSize = 16.sp)
            Text("  " + rec.title, color = P.WHITE, fontSize = 13.sp, maxLines = 1,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            PButton("✕", primary = false, height = 34.dp) { onClose() }
        }
        if (LocalConfiguration.current.orientation == Configuration.ORIENTATION_LANDSCAPE) {
            Row(Modifier.fillMaxSize()) {
                Preview(Modifier.weight(1f).fillMaxHeight())
                Controls(Modifier.width(380.dp).fillMaxHeight().verticalScroll(rememberScrollState()))
            }
        } else {
            Preview(Modifier.fillMaxWidth().weight(1f))
            Controls(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()))
        }
    }
}

/** Лента: кадры (или звуковая волна), ручки начала и конца, полоска воспроизведения.
 *  Палец рядом с ручкой — двигает её, в любом другом месте — перематывает. */
@Composable
fun Timeline(src: File, audio: Boolean, dur: Long, viewA: Long, viewB: Long, start: Long, end: Long, pos: Long,
             onStart: (Long, Boolean) -> Unit, onEnd: (Long, Boolean) -> Unit, onScrub: (Long, Boolean) -> Unit) {
    val slots = 10
    val frames = remember { mutableStateListOf<ImageBitmap?>().apply { repeat(slots) { add(null) } } }
    var wave by remember { mutableStateOf<ImageBitmap?>(null) }
    val span = (viewB - viewA).coerceAtLeast(1L)
    val startState = rememberUpdatedState(start)      // жест читает текущее положение ручек
    val endState = rememberUpdatedState(end)
    fun startNow() = startState.value
    fun endNow() = endState.value

    LaunchedEffect(viewA, viewB) {
        if (audio) {
            if (wave == null) wave = withContext(Dispatchers.IO) { Trim.waveform(src)?.asImageBitmap() }
            return@LaunchedEffect
        }
        withContext(Dispatchers.IO) {
            val r = MediaMetadataRetriever()
            try {
                r.setDataSource(src.absolutePath)
                // вблизи нужны точные кадры, издалека хватает ключевых (это быстрее)
                val opt = if (span < 30_000) MediaMetadataRetriever.OPTION_CLOSEST
                          else MediaMetadataRetriever.OPTION_CLOSEST_SYNC
                for (i in 0 until slots) {
                    if (!isActive) break
                    val us = (viewA + (i + 0.5) * span / slots).toLong() * 1000
                    val bmp = runCatching {
                        if (Build.VERSION.SDK_INT >= 27) r.getScaledFrameAtTime(us, opt, 240, 240)
                        else r.getFrameAtTime(us, opt)?.let { b ->
                            val k = 160f / max(b.height, 1)
                            Bitmap.createScaledBitmap(b, max((b.width * k).toInt(), 1), 160, true)
                        }
                    }.getOrNull()
                    frames[i] = bmp?.asImageBitmap()
                }
            } catch (_: Throwable) {
            } finally {
                runCatching { r.release() }
            }
        }
    }

    Canvas(
        Modifier.fillMaxWidth().padding(top = 6.dp).height(76.dp)
            .pointerInput(viewA, viewB, dur) {
                awaitEachGesture {
                    val down = awaitFirstDown()
                    val w = size.width.toFloat()
                    fun toMs(x: Float) = (viewA + (x / w).coerceIn(0f, 1f) * span).toLong().coerceIn(0L, dur)
                    fun toX(ms: Long) = (ms - viewA).toFloat() / span * w
                    val grab = 30.dp.toPx()
                    val ds = abs(down.position.x - toX(startNow()))
                    val de = abs(down.position.x - toX(endNow()))
                    val target = when {
                        ds <= grab && (ds < de || (ds == de && down.position.x < toX(startNow()))) -> "start"
                        de <= grab -> "end"
                        else -> "scrub"
                    }
                    fun emit(x: Float, done: Boolean) = when (target) {
                        "start" -> onStart(toMs(x), done)
                        "end" -> onEnd(toMs(x), done)
                        else -> onScrub(toMs(x), done)
                    }
                    var last = down.position.x
                    emit(last, false)
                    drag(down.id) { ch ->
                        last = ch.position.x
                        emit(last, false)
                        ch.consume()
                    }
                    emit(last, true)
                }
            }
    ) {
        val w = size.width
        val h = size.height
        val pad = 6.dp.toPx()
        val top = pad
        val sh = h - pad * 2
        fun x(ms: Long) = ((ms - viewA).toFloat() / span * w)
        drawRect(P.CARD2, Offset(0f, top), Size(w, sh))
        val wv = wave
        if (audio && wv != null) {
            val sx = (viewA.toFloat() / dur * wv.width).toInt().coerceIn(0, wv.width - 1)
            val sw = max((span.toFloat() / dur * wv.width).toInt(), 1)
            drawImage(wv, IntOffset(sx, 0), IntSize(max(min(sw, wv.width - sx), 1), wv.height),
                IntOffset(0, top.roundToInt()), IntSize(w.roundToInt(), sh.roundToInt()))
        } else if (!audio) {
            val sw = w / slots
            for (i in 0 until slots) {
                val f = frames[i] ?: continue
                // кадр заполняет ячейку целиком, лишнее по краям обрезается
                val k = max(sw / f.width, sh / f.height)
                val cw = min((sw / k).roundToInt(), f.width)
                val chh = min((sh / k).roundToInt(), f.height)
                drawImage(f, IntOffset((f.width - cw) / 2, (f.height - chh) / 2), IntSize(cw, chh),
                    IntOffset((i * sw).roundToInt(), top.roundToInt()),
                    IntSize(sw.roundToInt() + 1, sh.roundToInt()))
            }
        }
        val xs = x(start).coerceIn(0f, w)
        val xe = x(end).coerceIn(0f, w)
        // всё вне выделения затемнено
        drawRect(Color(0xB3000000), Offset(0f, top), Size(xs, sh))
        drawRect(Color(0xB3000000), Offset(xe, top), Size(w - xe, sh))
        val line = 3.dp.toPx()
        drawRect(P.RED, Offset(xs, top), Size(xe - xs, line))
        drawRect(P.RED, Offset(xs, top + sh - line), Size(xe - xs, line))
        // ручки: красные, выше ленты, с белой риской
        val hw = 14.dp.toPx()
        for ((hx, left) in listOf(xs to true, xe to false)) {
            if (x(if (left) start else end) < -1f || x(if (left) start else end) > w + 1f) continue
            val ox = if (left) hx - hw else hx
            drawRoundRect(P.RED, Offset(ox, 0f), Size(hw, h), CornerRadius(4.dp.toPx()))
            drawRect(P.WHITE, Offset(ox + hw / 2 - 1.dp.toPx(), h * 0.3f), Size(2.dp.toPx(), h * 0.4f))
        }
        // полоска воспроизведения
        val xp = x(pos)
        if (xp in 0f..w) {
            drawRect(P.WHITE, Offset(xp - 1.dp.toPx(), 0f), Size(2.dp.toPx(), h))
            drawRect(P.WHITE, Offset(xp - 4.dp.toPx(), 0f), Size(8.dp.toPx(), 4.dp.toPx()))
        }
    }
}
