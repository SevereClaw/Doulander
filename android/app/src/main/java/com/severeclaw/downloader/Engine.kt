package com.severeclaw.downloader

import android.content.Context
import android.content.Intent
import android.media.MediaScannerConnection
import android.os.Environment
import androidx.core.content.ContextCompat
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/** Всё, что делает программа: очередь, превью, загрузка, история, обновление yt-dlp. */
/** Подсказка в ошибках; по ней карточка показывает кнопку «Войти». */
const val LOGIN_HINT = "войдите в YouTube"

object Engine {
    lateinit var app: Context
        private set
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val ready = MutableStateFlow(false)
    val initError = MutableStateFlow<String?>(null)
    val items = MutableStateFlow<List<Item>>(emptyList())
    val history = MutableStateFlow<List<Rec>>(emptyList())
    val settings = MutableStateFlow(Settings())
    val log = MutableStateFlow<List<String>>(emptyList())
    val ytdlpVersion = MutableStateFlow<String?>(null)
    val updating = MutableStateFlow(false)
    val message = MutableStateFlow<String?>(null)      // короткое сообщение внизу экрана

    private val jobs = ConcurrentHashMap<String, Job>()
    private val stopped = ConcurrentHashMap.newKeySet<String>()
    private val durations = ConcurrentHashMap<String, Int>()
    private val lock = Any()
    private val gate = Any()                 // yt-dlp не обновляется, пока он работает, и наоборот
    private val active = AtomicInteger(0)
    private var finishedInSession = 0

    /** Сюда складываются готовые файлы: «Загрузки/Downloader». */
    val downloadDir: File
        get() = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
            "Downloader")

    /** QuickJS — движок JavaScript, без него YouTube отдаёт не все видео. */
    val qjs: File get() = File(app.applicationInfo.nativeLibraryDir, "libqjsng.so")

    fun init(c: Context) {
        app = c.applicationContext
        settings.value = Store.loadSettings(app)
        history.value = Store.loadHistory(app)
        Sync.init()
        // незаконченные загрузки прошлого запуска — снова в очередь
        items.value = Store.loadQueue(app).map {
            if (it.state == State.RUN) it.copy(state = State.WAIT, status = "В очереди") else it
        }
        scope.launch {
            try {
                YoutubeDL.getInstance().init(app)
                FFmpeg.getInstance().init(app)
                ytdlpVersion.value = runCatching { YoutubeDL.getInstance().version(app) }.getOrNull()
                log("Готово к работе. yt-dlp ${ytdlpVersion.value}, QuickJS: " +
                        if (qjs.exists()) "есть" else "нет")
                ready.value = true
                autoUpdate()
                pump()
            } catch (e: Throwable) {
                initError.value = e.message ?: e.toString()
                log("Не удалось запустить yt-dlp: $e")
            }
        }
    }

    fun log(text: String) {
        val time = SimpleDateFormat("HH:mm:ss", Locale.ROOT).format(Date())
        synchronized(lock) { log.value = (log.value + "$time  $text").takeLast(400) }
    }

    fun saveSettings(s: Settings) {
        settings.value = s
        Store.saveSettings(app, s)
    }

    // ------------------------------------------------------------------ очередь
    private fun update(id: String, f: (Item) -> Item) {
        synchronized(lock) {
            items.value = items.value.map { if (it.id == id) f(it) else it }
        }
    }

    private fun item(id: String) = items.value.firstOrNull { it.id == id }

    private fun saveQueue() = Store.saveQueue(app, items.value.filter { it.state != State.DONE }
        + items.value.filter { it.state == State.DONE }.take(50))

    /** Достаёт ссылку из текста («Посмотрите это видео: https://…»). Не ссылка — поиск по названию. */
    fun parseInput(text: String): List<String> {
        val urls = Regex("""https?://\S+""").findAll(text).map { it.value.trimEnd('.', ',', ')') }
            .toList()
        if (urls.isNotEmpty()) return urls
        val q = text.trim()
        return if (q.isEmpty()) emptyList() else listOf("ytsearch1:$q")
    }

    fun add(text: String, opts: Opts = settings.value.opts): Int {
        var added = 0
        for (url in parseInput(text)) {
            val busy = items.value.any {
                it.url == url && it.state in listOf(State.WAIT, State.RUN)
            }
            if (busy) {
                message.value = "Эта ссылка уже в очереди"
                continue
            }
            val item = Item(id = UUID.randomUUID().toString(), url = url, opts = opts,
                title = if (url.startsWith("ytsearch1:")) "Поиск: ${url.removePrefix("ytsearch1:")}"
                else null)
            synchronized(lock) { items.value = listOf(item) + items.value }
            added++
            scope.launch { preview(item.id) }
        }
        if (added > 0) {
            saveQueue()
            startService()
            pump()
        }
        return added
    }

    fun changeKind(id: String, opts: Opts) {
        val it = item(id) ?: return
        if (it.state == State.RUN) return
        partDir(id).deleteRecursively()      // недокачанное было в другом формате
        update(id) { i -> i.copy(opts = opts, status = "Будет скачано: ${opts.describe()}", progress = 0f,
            state = if (i.state == State.DONE) i.state else State.WAIT) }
        scope.launch { preview(id) }
        saveQueue()
        pump()
    }

    fun redownload(id: String, opts: Opts? = null) {
        val it = item(id) ?: return
        add(it.url, opts ?: it.opts)
    }

    fun redownload(rec: Rec, opts: Opts? = null) = add(rec.url, opts ?: rec.opts)

    /** Недокачанное лежит здесь до конца загрузки — отсюда она продолжается после паузы,
     *  ошибки связи или перезагрузки телефона. */
    fun partDir(id: String) = File(app.noBackupFilesDir, "dl/$id")

    private fun kill(id: String) {
        stopped.add(id)
        runCatching { YoutubeDL.getInstance().destroyProcessById(id) }
        jobs.remove(id)?.cancel()
    }

    /** Пауза: загрузка останавливается, скачанное сохраняется. */
    fun pause(id: String) {
        val it = item(id) ?: return
        if (it.state != State.RUN && it.state != State.WAIT) return
        kill(id)
        update(id) { i -> i.copy(state = State.PAUSED, status = pausedText(i)) }
        saveQueue()
        pump()
    }

    private fun pausedText(i: Item) =
        "⏸ Пауза" + if (i.progress > 0f) " — ${(i.progress * 100).toInt()}%" else ""

    /** Продолжить с того же места (после паузы, ошибки или остановки). */
    fun resume(id: String) {
        stopped.remove(id)
        update(id) { it.copy(state = State.WAIT, status = "В очереди — продолжу с места остановки") }
        saveQueue()
        startService()
        pump()
    }

    fun retry(id: String) = resume(id)

    /** Начать заново: выбросить скачанное. */
    fun restart(id: String) {
        kill(id)
        partDir(id).deleteRecursively()
        stopped.remove(id)
        update(id) { it.copy(state = State.WAIT, status = "В очереди", progress = 0f) }
        saveQueue()
        startService()
        pump()
    }

    /** Отменить: остановить и удалить недокачанное. */
    fun stop(id: String) {
        kill(id)
        partDir(id).deleteRecursively()
        update(id) { it.copy(state = State.STOPPED, status = "Отменено", progress = 0f) }
        saveQueue()
        pump()
    }

    fun pauseAll() {
        items.value.filter { it.state == State.RUN || it.state == State.WAIT }.forEach { pause(it.id) }
    }

    fun resumeAll() {
        items.value.filter { it.state == State.PAUSED }.forEach { resume(it.id) }
    }

    fun remove(id: String) {
        val st = item(id)?.state
        if (st == State.RUN) kill(id)
        if (st != State.DONE) partDir(id).deleteRecursively()
        synchronized(lock) { items.value = items.value.filter { it.id != id } }
        saveQueue()
    }

    fun clearDone() {
        synchronized(lock) { items.value = items.value.filter { it.state != State.DONE } }
        saveQueue()
    }

    fun startService() {
        runCatching {
            ContextCompat.startForegroundService(app, Intent(app, DownloadService::class.java))
        }.onFailure { log("Служба загрузки не запустилась: $it") }
    }

    /** Запускает следующие загрузки, пока не занято «одновременно». */
    @Synchronized
    private fun pump() {
        if (!ready.value || updating.value) return
        val limit = settings.value.parallel
        while (true) {
            val running = items.value.count { it.state == State.RUN }
            if (running >= limit) return
            // самая старая ожидающая ссылка (новые добавляются в начало списка)
            val next = items.value.lastOrNull { it.state == State.WAIT } ?: break
            update(next.id) { it.copy(state = State.RUN, status = "Начинаю…", progress = 0f) }
            stopped.remove(next.id)
            jobs[next.id] = scope.launch {
                try {
                    download(next.id)
                } finally {
                    jobs.remove(next.id)
                    saveQueue()
                    pump()
                }
            }
        }
        if (items.value.none { it.state == State.RUN || it.state == State.WAIT } &&
            finishedInSession > 0) {
            onAllDone()
        }
    }

    private fun onAllDone() {
        val n = finishedInSession
        finishedInSession = 0
        val open = settings.value.openFolder
        DownloadService.notifyDone(app, n, settings.value.sound, open)
        // приложение на экране — папка открывается сразу; свёрнуто — по нажатию на уведомление
        // (Android не разрешает открывать окна из фона)
        if (open && MainActivity.visible) runCatching { app.startActivity(folderIntent()) }
    }

    // ------------------------------------------------------------------ yt-dlp
    /** Запуск yt-dlp: ждёт, пока он подготовится и пока не идёт его обновление. */
    private suspend fun <T> ytdlp(block: () -> T): T {
        while (true) {
            val ok = synchronized(gate) {
                if (ready.value && !updating.value) { active.incrementAndGet(); true } else false
            }
            if (ok) break
            delay(300)
        }
        try {
            return block()
        } finally {
            active.decrementAndGet()
        }
    }

    private fun commonOptions(req: YoutubeDLRequest) {
        req.addOption("--no-warnings")
        req.addOption("--socket-timeout", "20")
        req.addOption("--retries", "10")
        req.addOption("--extractor-retries", "5")
        val proxy = settings.value.proxy.trim()
        if (proxy.isNotEmpty()) req.addOption("--proxy", proxy)
        if (qjs.exists()) req.addOption("--js-runtimes", "quickjs:${qjs.absolutePath}")
        // вход в YouTube (⚙ Настройки → «Войти в YouTube»)
        if (YouTubeCookies.present) req.addOption("--cookies", YouTubeCookies.file.absolutePath)
    }

    /** Ошибка из-за того, что YouTube просит вход. */
    fun isLoginError(err: String): Boolean {
        val low = err.lowercase()
        return "not a bot" in low || "confirm you" in low || "sign in to confirm your age" in low ||
            "age-restricted" in low || "members-only" in low || "join this channel" in low ||
            "login required" in low || "use --cookies" in low
    }

    /** После входа — повторить загрузки, которые упали из-за просьбы войти. */
    fun retryLoginErrors() {
        items.value.filter { it.state == State.ERROR && it.status.contains("войдите") }
            .forEach { retry(it.id) }
        items.value.filter { it.meta.contains("войдите") }.forEach { i -> scope.launch { preview(i.id) } }
    }

    private fun formatOptions(req: YoutubeDLRequest, o: Opts) {
        if (o.isAudio) {
            req.addOption("-f", "ba/b")
            return
        }
        val sort = mutableListOf(if (o.quality == "best") "res" else "res:${o.quality}")
        when (o.fmt) {
            "mp4" -> sort += "ext:mp4:m4a"
            "webm" -> sort += "ext:webm:webm"
        }
        req.addOption("-f", "bv*+ba/b")
        req.addOption("-S", sort.joinToString(","))
    }

    /** Название, обложка, длительность и примерный размер — до загрузки. */
    private suspend fun preview(id: String) {
        val it = item(id) ?: return
        val req = YoutubeDLRequest(it.url)
        req.addOption("-J")
        req.addOption(if (it.opts.playlist) "--flat-playlist" else "--no-playlist")
        commonOptions(req)
        if (!it.opts.playlist) formatOptions(req, it.opts)
        var info: JSONObject? = null
        var err = ""
        for (attempt in 1..3) {
            try {
                val out = ytdlp { YoutubeDL.getInstance().execute(req).out }
                info = JSONObject(out.substring(out.indexOf('{')))
                break
            } catch (e: Throwable) {
                err = lastError(e.message ?: e.toString())
                if (!isNetError(err) || attempt == 3) break
                update(id) { i -> i.copy(meta = "Сбой связи — пробую ещё раз ($attempt/3)…") }
                delay(2000L * attempt)
            }
        }
        if (info == null) {
            log("Превью ${it.url}: $err")
            update(id) { i -> i.copy(meta = explain(err)) }
            return
        }
        // поиск по названию: yt-dlp возвращает «плейлист» из одного видео
        val entry = info.optJSONArray("entries")?.optJSONObject(0)
            ?.takeIf { _ -> it.url.startsWith("ytsearch") } ?: info
        val title = entry.optString("title").ifEmpty { null }
        val dur = entry.optDouble("duration", 0.0).toInt().takeIf { d -> d > 0 }
        if (dur != null) durations[id] = dur
        val count = info.optJSONArray("entries")?.length()
        var size = entry.optLong("filesize").takeIf { s -> s > 0 }
            ?: entry.optLong("filesize_approx").takeIf { s -> s > 0 }
        entry.optJSONArray("requested_formats")?.let { a ->
            var sum = 0L
            for (n in 0 until a.length()) {
                val f = a.getJSONObject(n)
                sum += f.optLong("filesize").takeIf { s -> s > 0 } ?: f.optLong("filesize_approx")
            }
            if (sum > 0) size = sum
        }
        if (it.opts.isAudio && dur != null) {
            // для звука считаем по битрейту — видео в размере не нужно
            val kbps = when (it.opts.fmt) { "flac" -> 900; "opus" -> 128; else ->
                it.opts.quality.toIntOrNull() ?: 256 }
            size = dur.toLong() * kbps * 1000 / 8
        }
        val thumb = entry.optString("thumbnail").ifEmpty {
            entry.optJSONArray("thumbnails")?.let { a ->
                if (a.length() > 0) a.getJSONObject(a.length() - 1).optString("url") else null
            } ?: ""
        }.ifEmpty { null }
        val parts = listOfNotNull(
            entry.optString("uploader").ifEmpty { entry.optString("channel") }.ifEmpty { null },
            if (it.opts.playlist && count != null) "плейлист: $count" else humanDuration(dur),
            if (it.opts.playlist) null else humanSize(size)?.let { s -> "≈ $s" },
            it.opts.describe(),
        )
        update(id) { i ->
            i.copy(title = if (it.opts.playlist) info.optString("title").ifEmpty { title } else title,
                thumb = thumb ?: i.thumb, meta = parts.joinToString("  •  "))
        }
        saveQueue()
    }

    private val speedRe = Regex("""at\s+([\d.]+\s*\S+/s)""")
    private val etaRe = Regex("""ETA\s+(\S+)""")

    private suspend fun download(id: String) {
        val start = item(id) ?: return
        // не очищаем: если там есть недокачанное — yt-dlp продолжит с того же места
        val tmp = partDir(id).apply { mkdirs() }
        if (tmp.walkTopDown().any { it.isFile }) log("Продолжаю с места остановки: ${start.label}")
        if (start.pcId != null) {
            // файл с ПК — забираем по Wi-Fi
            val err = pullFromPc(id, start, tmp)
            if (id in stopped) return
            if (err != null) {
                update(id) { it.copy(state = State.ERROR, status = err) }
                return
            }
        } else {
            var attempt = 1
            var lastErr = ""
            while (true) {
                val it = item(id) ?: return
                val req = buildRequest(it, tmp)
                try {
                    update(id) { i -> i.copy(status = "Подключаюсь…") }
                    ytdlp {
                        YoutubeDL.getInstance().execute(req, id) { progress, _, line ->
                            onLine(id, progress, line)
                        }
                    }
                    break
                } catch (e: Throwable) {
                    if (id in stopped) return
                    lastErr = lastError(e.message ?: e.toString())
                    log("Ошибка ${it.url}: $lastErr")
                    if (isNetError(lastErr) && attempt < 3) {
                        attempt++
                        update(id) { i -> i.copy(status = "Сбой связи — пробую ещё раз ($attempt/3)…") }
                        delay(3000L * attempt)
                        continue
                    }
                    update(id) { i -> i.copy(state = State.ERROR, status = explain(lastErr)) }
                    return
                }
            }
        }
        if (id in stopped) return
        update(id) { it.copy(status = "Сохраняю в «Загрузки»…") }
        val saved = try {
            moveOut(tmp, downloadDir)
        } catch (e: Throwable) {
            update(id) { it.copy(state = State.ERROR, status = "Не удалось сохранить: ${e.message}") }
            return
        }
        tmp.deleteRecursively()
        if (saved.isEmpty()) {
            update(id) { it.copy(state = State.ERROR, status = "yt-dlp ничего не скачал") }
            return
        }
        MediaScannerConnection.scanFile(app, saved.map { it.absolutePath }.toTypedArray(), null, null)
        val done = item(id) ?: start
        update(id) {
            it.copy(state = State.DONE, progress = 1f, files = saved.map { f -> f.absolutePath },
                status = "✓ Готово — " + if (saved.size == 1) saved[0].name else "файлов: ${saved.size}")
        }
        val now = System.currentTimeMillis()
        val recs = saved.map { f ->
            Rec(url = done.url, title = if (saved.size == 1) done.label else f.nameWithoutExtension,
                file = f.absolutePath, thumb = done.thumb, date = now, opts = done.opts,
                duration = if (saved.size == 1) durations[id] else null)
        }
        synchronized(lock) { history.value = recs + history.value }
        Store.saveHistory(app, history.value)
        finishedInSession++
        log("Скачано: ${saved.joinToString { it.name }}")
        if (Sync.paired) scope.launch { Sync.sync(quiet = true) }   // чтобы ПК сразу увидел
    }

    /** Забрать файл с ПК по Wi-Fi. null — получилось, иначе текст ошибки. */
    private fun pullFromPc(id: String, it: Item, tmp: File): String? {
        val name = (it.pcName ?: "файл").replace(Regex("""[\\/:*?"<>|]"""), "_")
        val out = File(tmp, name)
        val part = File(tmp, "$name.part")             // недокачанное — докачивается с места остановки
        try {
            update(id) { i -> i.copy(status = "Подключаюсь к ПК…") }
            val have = if (part.exists()) part.length() else 0L
            val c = Sync.conn(Sync.fileUrl(it.pcId!!), timeout = 8000)
            if (have > 0) c.setRequestProperty("Range", "bytes=$have-")
            val resumed = c.responseCode == 206
            when (c.responseCode) {
                200, 206 -> {}
                401 -> return "ПК не принимает этот телефон — подключитесь заново в настройках"
                404 -> return "Этого файла больше нет на ПК"
                else -> return "ПК ответил: ${c.responseCode}"
            }
            val offset = if (resumed) have else 0L
            val total = c.contentLengthLong.let { if (it > 0) it + offset else it }
            val started = System.currentTimeMillis()
            c.inputStream.use { inp ->
                java.io.FileOutputStream(part, resumed).use { o ->
                    val buf = ByteArray(256 * 1024)
                    var done = offset
                    var last = 0L
                    while (true) {
                        if (id in stopped) return null
                        val n = inp.read(buf)
                        if (n < 0) break
                        o.write(buf, 0, n)
                        done += n
                        if (done - last > 1024 * 1024 || done == total) {
                            last = done
                            val sec = (System.currentTimeMillis() - started).coerceAtLeast(1) / 1000.0
                            val speed = humanSize(((done - offset) / sec).toLong())
                            update(id) { i ->
                                i.copy(progress = if (total > 0) done.toFloat() / total else 0f,
                                    status = listOfNotNull(
                                        if (total > 0) "${done * 100 / total}%" else humanSize(done),
                                        speed?.let { s -> "$s/с" }, "💻 по Wi-Fi").joinToString("  •  "))
                            }
                        }
                    }
                    if (total > 0 && done != total) return "Связь с ПК оборвалась — нажмите ⟳, продолжу с места"
                }
            }
            part.renameTo(out)
            return null
        } catch (e: Throwable) {
            return if (id in stopped) null else "ПК не отвечает — он включён и в той же сети Wi-Fi?"
        }
    }

    /** Забрать файл с ПК (из галереи). */
    fun addFromPc(r: PcRec) {
        if (items.value.any { it.pcId == r.id && it.state in listOf(State.WAIT, State.RUN) }) {
            message.value = "Этот файл уже забирается"
            return
        }
        val item = Item(id = UUID.randomUUID().toString(), url = r.url.ifEmpty { "pc:${r.id}" },
            opts = r.opts, title = r.title, thumb = Sync.thumbUrl(r.id),
            meta = listOfNotNull("💻 с ПК", humanDuration(r.duration), humanSize(r.size), r.ext.uppercase())
                .joinToString("  •  "),
            pcId = r.id, pcName = r.name)
        if (r.duration != null) durations[item.id] = r.duration
        synchronized(lock) { items.value = listOf(item) + items.value }
        saveQueue()
        startService()
        pump()
        message.value = "Забираю с ПК: ${r.title}"
    }

    private fun onLine(id: String, progress: Float, line: String) {
        if (id in stopped) return                  // пауза: поздние строки не трогают карточку
        if ("Resuming download at byte" in line || "Resuming download from" in line) log(line.trim())
        when {
            progress >= 0 && line.contains("[download]") -> {
                val speed = speedRe.find(line)?.groupValues?.get(1)
                val eta = etaRe.find(line)?.groupValues?.get(1)?.takeIf { it != "Unknown" }
                val text = listOfNotNull("${progress.toInt()}%", speed,
                    eta?.let { "осталось $it" }).joinToString("  •  ")
                update(id) { it.copy(progress = progress / 100f, status = text) }
            }
            line.startsWith("[Merger]") || line.startsWith("[VideoRemuxer]") ->
                update(id) { it.copy(status = "Склеиваю видео и звук…") }
            line.startsWith("[ExtractAudio]") ->
                update(id) { it.copy(status = "Перевожу в ${it.opts.fmt.uppercase()}…") }
            line.startsWith("[SponsorBlock]") || line.startsWith("[ModifyChapters]") ->
                update(id) { it.copy(status = "Вырезаю рекламу…") }
            line.startsWith("[EmbedThumbnail]") || line.startsWith("[Metadata]") ->
                update(id) { it.copy(status = "Вшиваю обложку и теги…") }
        }
    }

    private fun buildRequest(it: Item, tmp: File): YoutubeDLRequest {
        val o = it.opts
        val req = YoutubeDLRequest(it.url)
        commonOptions(req)
        req.addOption("--newline")
        req.addOption("-N", "4")                       // куски видео — параллельно
        req.addOption("--fragment-retries", "10")
        req.addOption("--windows-filenames")           // без символов, которые Android не любит
        req.addOption("--no-mtime")
        req.addOption(if (o.playlist) "--yes-playlist" else "--no-playlist")
        val name = "%(title).120B.%(ext)s"
        req.addOption("-o", File(tmp,
            if (o.playlist) "%(playlist_title).80B/%(playlist_index)03d. $name" else name).path)
        formatOptions(req, o)
        if (o.isAudio) {
            req.addOption("-x")
            req.addOption("--audio-format", o.fmt)
            if (o.fmt != "flac") {
                req.addOption("--audio-quality", if (o.quality == "best") "0" else "${o.quality}K")
            }
            if (o.fmt == "mp3" || o.fmt == "m4a") {
                // обложка трека — квадратная, по центру кадра, как у альбома
                req.addOption("--embed-thumbnail")
                req.addOption("--convert-thumbnails", "webp>jpg/png>jpg/jpg>png")
                req.addOption("--ppa", "ThumbnailsConvertor:-qmin 1 -q:v 1 -vf " +
                        "crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\"")
            }
        } else {
            req.addOption("--merge-output-format", if (o.fmt == "webm") "webm/mkv" else o.fmt)
            when (o.fmt) {
                "mkv" -> req.addOption("--remux-video", "mkv")
                "webm" -> req.addOption("--remux-video", "webm>webm/mkv")
            }
            if (o.fmt != "webm") req.addOption("--embed-thumbnail")
        }
        req.addOption("--embed-metadata")
        if (o.noAds) req.addOption("--sponsorblock-remove", "sponsor,selfpromo,interaction")
        return req
    }

    /** Переносит готовые файлы из временной папки в «Загрузки/Downloader», не затирая старые. */
    private fun moveOut(tmp: File, dest: File): List<File> {
        val out = mutableListOf<File>()
        tmp.walkTopDown().filter { it.isFile && !it.name.endsWith(".part") &&
                !it.name.contains(".part-Frag") && !it.name.endsWith(".ytdl") &&
                !it.name.endsWith(".json") && !it.name.endsWith(".temp") }.forEach { f ->
            val rel = f.relativeTo(tmp).path
            var target = File(dest, rel)
            target.parentFile?.mkdirs()
            var n = 2
            while (target.exists()) {
                target = File(target.parentFile, "${f.nameWithoutExtension} ($n).${f.extension}")
                n++
            }
            if (!f.renameTo(target)) {
                f.copyTo(target, overwrite = false)
                f.delete()
            }
            out += target
        }
        return out.sortedBy { it.path }
    }

    // ------------------------------------------------------------------ ошибки
    private fun lastError(text: String): String {
        val lines = text.lines().map { it.trim() }.filter { it.isNotEmpty() }
        return (lines.lastOrNull { it.startsWith("ERROR") } ?: lines.lastOrNull() ?: text)
            .removePrefix("ERROR:").trim()
    }

    fun isNetError(err: String): Boolean {
        val low = err.lowercase()
        return listOf("timed out", "timeout", "connection", "network", "ssl", "eof occurred",
            "unable to download api page", "remote end closed", "reset by peer",
            "temporary failure in name resolution", "unreachable", "incompleteread")
            .any { it in low }
    }

    fun explain(err: String): String {
        val low = err.lowercase()
        return when {
            "not a bot" in low || "confirm you" in low && "bot" in low ->
                if (YouTubeCookies.present) "YouTube всё равно просит вход — войдите заново " +
                    "(⚙ Настройки) или смените сервер VPN"
                else "YouTube просит вход («вы не бот?») — $LOGIN_HINT"
            "sign in to confirm your age" in low || "age-restricted" in low ->
                if (YouTubeCookies.present) "Видео 18+: аккаунту нужно подтвердить возраст в YouTube"
                else "Видео 18+ — $LOGIN_HINT"
            "members-only" in low || "join this channel" in low ->
                "Видео только для спонсоров канала" + if (YouTubeCookies.present) "" else " — $LOGIN_HINT"
            "private video" in low -> "Это закрытое видео"
            "video unavailable" in low || "not available" in low -> "Видео недоступно"
            "unsupported url" in low -> "Этот сайт не поддерживается"
            "http error 403" in low -> "Сайт запретил скачивание (403) — обновите yt-dlp в настройках"
            "http error 404" in low -> "Не найдено (404) — проверьте ссылку"
            "no space left" in low -> "Закончилось место в телефоне"
            isNetError(err) -> if (settings.value.proxy.isNotBlank())
                "Нет связи через прокси — проверьте VPN-приложение" else
                "Нет связи с сайтом — проверьте интернет или включите VPN"
            else -> "Ошибка: " + err.take(160)
        }
    }

    // ------------------------------------------------------------------ обновление yt-dlp
    private suspend fun autoUpdate() {
        val s = settings.value
        if (System.currentTimeMillis() - s.lastYtdlpUpdate < 20 * 3600 * 1000L) return
        updateYtdlp(manual = false)
    }

    suspend fun updateYtdlp(manual: Boolean) {
        synchronized(gate) {
            if (updating.value) return
            updating.value = true
        }
        try {
            // новые запуски уже ждут; дожидаемся, пока закончатся текущие загрузки
            if (active.get() > 0) {
                log("Обновление yt-dlp подождёт окончания загрузок")
                if (manual) message.value = "yt-dlp обновится, когда закончатся загрузки"
            }
            while (active.get() > 0) delay(500)
            val status = YoutubeDL.getInstance().updateYoutubeDL(app, YoutubeDL.UpdateChannel.STABLE)
            ytdlpVersion.value = runCatching { YoutubeDL.getInstance().version(app) }.getOrNull()
            saveSettings(settings.value.copy(lastYtdlpUpdate = System.currentTimeMillis()))
            log("yt-dlp: $status, версия ${ytdlpVersion.value}")
            if (manual) message.value = "yt-dlp: версия ${ytdlpVersion.value}"
        } catch (e: Throwable) {
            log("Не удалось обновить yt-dlp: ${e.message}")
            if (manual) message.value = "Не удалось обновить yt-dlp — нет связи с GitHub?"
        } finally {
            updating.value = false
            pump()
        }
    }

    // ------------------------------------------------------------------ файлы
    /** Новая запись истории не из загрузки (обрезка из редактора). */
    fun addToHistory(rec: Rec) {
        synchronized(lock) { history.value = listOf(rec) + history.value }
        Store.saveHistory(app, history.value)
        log("Сохранено: ${File(rec.file).name}")
        if (Sync.paired) scope.launch { Sync.sync(quiet = true) }
    }

    fun deleteFile(path: String): Boolean {
        val f = File(path)
        val ok = !f.exists() || f.delete()
        if (ok) {
            MediaScannerConnection.scanFile(app, arrayOf(path), null, null)
            synchronized(lock) { history.value = history.value.filter { it.file != path } }
            Store.saveHistory(app, history.value)
        }
        return ok
    }

    // ------------------------------------------------------------------ самопроверка (CI)
    /** Запуск из автосборки: скачивает тестовое видео в MP4 и MP3 и пишет итог в файл. */
    suspend fun selftest(url: String, out: File, syncHost: String? = null, syncCode: String? = null,
                         slowUrl: String? = null) {
        val lines = mutableListOf<String>()
        var failed = 0
        fun w(s: String) {
            lines += s
            runCatching { out.writeText(lines.joinToString("\n")) }
            android.util.Log.i("DLSELFTEST", s)      // сервер сборки читает отчёт через logcat
            log(s)
        }
        w("Самопроверка Downloader для Android ${BuildConfig.VERSION_NAME}")
        repeat(240) { if (!ready.value && initError.value == null) delay(500) }
        if (!ready.value) {
            w("FAIL yt-dlp не запустился: ${initError.value}"); w("Итог: ЕСТЬ ОШИБКИ"); return
        }
        while (updating.value) delay(500)
        w("OK   yt-dlp ${ytdlpVersion.value}")
        // QuickJS — запускается ли на этом процессоре
        try {
            val p = ProcessBuilder(qjs.absolutePath, "-e", "print(6*7)").redirectErrorStream(true)
                .start()
            val o = p.inputStream.bufferedReader().readText().trim()
            p.waitFor()
            if (o == "42") w("OK   QuickJS работает") else { failed++; w("FAIL QuickJS: $o") }
        } catch (e: Throwable) {
            failed++; w("FAIL QuickJS: $e")
        }
        // вход в YouTube: файл cookies в формате yt-dlp; загрузка MP4 ниже идёт уже с ним
        val net = YouTubeCookies.toNetscape("PREF=f6=40000000; SAPISID=abc/def; __Host-3PLSID=x=y")
        val netOk = YouTubeCookies.loggedIn("A=1; SAPISID=abc") && !YouTubeCookies.loggedIn("PREF=1") &&
            ".youtube.com\tTRUE\t/\tTRUE\t" in net && "\tSAPISID\tabc/def" in net &&
            "www.youtube.com\tFALSE\t/\tTRUE\t" in net && "\t__Host-3PLSID\tx=y" in net
        if (!netOk) failed++
        w("${if (netOk) "OK  " else "FAIL"} вход в YouTube: файл cookies")
        YouTubeCookies.file.writeText(net)
        for ((name, opts, ext) in listOf(
            Triple("видео MP4 720p", Opts("video", "720", "mp4"), "mp4"),
            Triple("видео MKV", Opts("video", "best", "mkv"), "mkv"),
            Triple("аудио MP3", Opts("audio", "best", "mp3"), "mp3"),
        )) {
            synchronized(lock) { items.value = emptyList() }
            add(url, opts)
            val id = items.value.firstOrNull()?.id
            var t = 0
            while (t < 240 && item(id ?: "")?.state.let { it == State.WAIT || it == State.RUN }) {
                delay(500); t++
            }
            val it = item(id ?: "")
            val file = it?.files?.firstOrNull()
            val ok = it?.state == State.DONE && file != null && file.endsWith(".$ext") &&
                    File(file).length() > 1000
            if (!ok) failed++
            w("${if (ok) "OK  " else "FAIL"} $name: ${it?.state} ${it?.status} " +
                    "${file?.let { f -> File(f).name + " " + File(f).length() + " Б" }}  [${it?.meta}]")
        }
        // обновление приложения: выбор релиза под процессор и сравнение номера сборки
        val fake = """[{"tag_name":"v1.15.0","assets":[{"name":"Downloader.zip","url":"x"}]},
            {"tag_name":"android-v1.1.0-50","body":"новое","assets":[
              {"name":"Downloader-arm64.apk","url":"u64","size":5},
              {"name":"Downloader-armv7.apk","url":"u7","size":4}]},
            {"tag_name":"android-v1.0.0-7","assets":[{"name":"Downloader-arm64.apk","url":"old"}]}]"""
        val r64 = Updater.pick(fake, listOf("arm64-v8a", "armeabi-v7a"))
        val r7 = Updater.pick(fake, listOf("armeabi-v7a"))
        val pickOk = r64?.build == 50 && r64.assetUrl == "u64" && r64.version == "1.1.0" &&
                r7?.assetUrl == "u7" && Updater.pick(fake, listOf("x86")) == null
        if (!pickOk) failed++
        w("${if (pickOk) "OK  " else "FAIL"} обновления: выбор релиза ($r64)")
        YouTubeCookies.file.delete()
        // редактор: обрезка скачанного MP4 «Быстро», «Точно» и «Только звук», звуковая волна
        history.value.firstOrNull { it.file.endsWith(".mp4") }?.let { r ->
            for ((name, exact, snd, ext) in listOf(
                listOf("быстро", false, "on", "mp4"), listOf("точно", true, "on", "mp4"),
                listOf("только звук", false, "only", "mp3"))) {
                val (f, err) = Trim.export(r, 1000, 3000, exact as Boolean, snd as String)
                val d = f?.let { Trim.durationMs(it) } ?: 0L
                // «Быстро» режет по ключевым кадрам — допуск больше
                val okLen = if (exact) d in 1800..2300 else d in 1500..4200
                val ok = f != null && f.extension == ext && f.name.contains("(обрезка 0'01–0'03)") && okLen
                if (!ok) failed++
                w("${if (ok) "OK  " else "FAIL"} редактор, $name: ${f?.name} ${f?.length()} Б, " +
                        "длительность $d мс ${err ?: ""}")
            }
            val wave = Trim.waveform(File(r.file))
            val wOk = wave != null && wave.width == 1600
            if (!wOk) failed++
            w("${if (wOk) "OK  " else "FAIL"} редактор: звуковая волна ${wave?.width}x${wave?.height}")
        } ?: run { failed++; w("FAIL редактор: нет MP4 для проверки") }
        // пауза и продолжение с места остановки (медленная раздача ~1,3 МБ/с)
        if (slowUrl != null) {
            synchronized(lock) { items.value = emptyList() }
            add(slowUrl, Opts("video", "best", "mp4"))
            val id = items.value.firstOrNull()?.id ?: ""
            var t = 0
            while (t < 240 && (item(id)?.progress ?: 0f) < 0.25f && item(id)?.state != State.DONE) {
                delay(250); t++
            }
            val at = item(id)?.progress ?: 0f
            pause(id)
            delay(2000)
            val partBytes = partDir(id).walkTopDown().filter { it.isFile }.sumOf { it.length() }
            val savedPaused = Store.loadQueue(app).firstOrNull { it.id == id }?.state == State.PAUSED
            val pausedOk = item(id)?.state == State.PAUSED && partBytes > 0 && savedPaused
            if (!pausedOk) failed++
            w("${if (pausedOk) "OK  " else "FAIL"} пауза на ${(at * 100).toInt()}%: ${item(id)?.status}, " +
                    "сохранено $partBytes Б, в очереди после перезапуска: ${if (savedPaused) "пауза" else "НЕТ"}")
            resume(id)
            t = 0
            while (t < 240 && item(id)?.state.let { it == State.WAIT || it == State.RUN }) { delay(500); t++ }
            val it = item(id)
            val f = it?.files?.firstOrNull()?.let { File(it) }
            val resumedLog = log.value.any { "Resuming download" in it }
            val resumeOk = it?.state == State.DONE && f != null && f.length() > 5_000_000 && resumedLog
            if (!resumeOk) failed++
            w("${if (resumeOk) "OK  " else "FAIL"} продолжение с места остановки: ${it?.state} " +
                    "${f?.name} ${f?.length()} Б, yt-dlp продолжил: ${if (resumedLog) "да" else "НЕТ"}")
        }
        // синхронизация с ПК (на сервере сборки — тестовый «ПК» из sync_server.py)
        if (syncHost != null && syncCode != null) {
            val wrong = Sync.pair(syncHost, "000000")
            val err = Sync.pair(syncHost, syncCode)
            val pcList = Sync.pcItems.value
            val okPair = wrong != null && err == null && pcList.isNotEmpty()
            if (!okPair) failed++
            w("${if (okPair) "OK  " else "FAIL"} синхронизация: подключение (неверный код: $wrong; " +
                    "верный: ${err ?: "ок"}), файлов на ПК: ${pcList.size}")
            pcList.firstOrNull()?.let { r ->
                synchronized(lock) { items.value = emptyList() }
                addFromPc(r)
                val id = items.value.firstOrNull()?.id ?: ""
                var t = 0
                while (t < 120 && item(id)?.state.let { it == State.WAIT || it == State.RUN }) { delay(500); t++ }
                val it = item(id)
                val f = it?.files?.firstOrNull()?.let { File(it) }
                val okPull = it?.state == State.DONE && f != null && f.length() == r.size
                if (!okPull) failed++
                w("${if (okPull) "OK  " else "FAIL"} синхронизация: файл с ПК ${it?.status} " +
                        "${f?.name} ${f?.length()} из ${r.size} Б")
            }
            val pcApk = Updater.fromPc()
            val apkOk = pcApk?.build == 999 && pcApk.assetName == "Downloader-x86_64.apk"
            if (!apkOk) failed++
            w("${if (apkOk) "OK  " else "FAIL"} обновление с ПК: ${pcApk?.version} (сборка ${pcApk?.build}) " +
                    "${pcApk?.assetName}; открытый репозиторий: ${Updater.fromPublic()?.tag ?: "пока пуст"}")
            val pushed = Sync.sync(quiet = true)
            if (!pushed) failed++
            w("${if (pushed) "OK  " else "FAIL"} синхронизация: история отправлена на ПК (${Sync.localRecords().length()})")
        }
        val inHistory = history.value.size >= 3
        if (!inHistory) failed++
        w("${if (inHistory) "OK  " else "FAIL"} история: записей ${history.value.size}")
        w(if (failed == 0) "Итог: ВСЁ ХОРОШО" else "Итог: ЕСТЬ ОШИБКИ ($failed)")
    }
}
