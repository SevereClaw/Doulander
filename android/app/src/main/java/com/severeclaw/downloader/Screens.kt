package com.severeclaw.downloader

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.webkit.MimeTypeMap
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.ExperimentalLayoutApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import coil.compose.AsyncImage
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

// =================================================================== каркас
@Composable
fun DownloaderApp(tab: String, onTab: (String) -> Unit) {
    val msg by Engine.message.collectAsState()
    LaunchedEffect(msg) {
        if (msg != null) { delay(3500); Engine.message.value = null }
    }
    Column(Modifier.fillMaxSize().background(P.BG)) {
        Banner()
        Box(Modifier.weight(1f).fillMaxWidth()) {
            when (tab) {
                "gallery" -> GalleryScreen()
                "settings" -> SettingsScreen()
                else -> MainScreen()
            }
            msg?.let {
                Text(it, color = P.WHITE, fontSize = 14.sp,
                    modifier = Modifier.align(Alignment.BottomCenter).padding(12.dp)
                        .background(P.RED_DARK).padding(horizontal = 14.dp, vertical = 10.dp))
            }
        }
        Row(Modifier.fillMaxWidth().background(Color.Black).padding(8.dp),
            horizontalArrangement = Arrangement.SpaceEvenly) {
            for ((key, label) in listOf("main" to "Загрузки", "gallery" to "Галерея",
                "settings" to "Настройки")) {
                PChip(label.uppercase(), tab == key) { onTab(key) }
            }
        }
    }
}

// =================================================================== главный экран
@Composable
fun MainScreen() {
    val ctx = LocalContext.current
    val settings by Engine.settings.collectAsState()
    val items by Engine.items.collectAsState()
    val ready by Engine.ready.collectAsState()
    val initError by Engine.initError.collectAsState()
    var input by remember { mutableStateOf("") }
    val opts = settings.opts
    fun setOpts(o: Opts) = Engine.saveSettings(settings.copy(opts = o))
    fun submit() {
        if (input.isBlank()) return
        if (Engine.add(input, opts) > 0) input = ""
    }

    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 12.dp)) {
        item {
            Spacer(Modifier.height(6.dp))
            PField(input, { input = it }, "Ссылка или название песни",
                Modifier.fillMaxWidth(), onDone = ::submit)
            Row(Modifier.padding(top = 8.dp)) {
                PButton("Вставить", primary = false) {
                    val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                    val t = cm.primaryClip?.getItemAt(0)?.coerceToText(ctx)?.toString()
                    if (!t.isNullOrBlank()) input = t.trim()
                }
                Spacer(Modifier.width(8.dp))
                PButton("+ Скачать", Modifier.weight(1f), enabled = input.isNotBlank()) { submit() }
            }
            KindPicker(opts, ::setOpts)
            when {
                initError != null -> Text("Не удалось запустить yt-dlp: $initError", color = P.RED,
                    modifier = Modifier.padding(top = 8.dp))
                !ready -> Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(16.dp), color = P.RED, strokeWidth = 2.dp)
                    Text("  Готовлю yt-dlp (первый запуск — до минуты)…", color = P.MUTED,
                        fontSize = 13.sp)
                }
            }
            Row(Modifier.fillMaxWidth().padding(top = 14.dp, bottom = 6.dp),
                verticalAlignment = Alignment.CenterVertically) {
                PTitle(R.drawable.t_queue)
                Spacer(Modifier.weight(1f))
                if (items.any { it.state == State.DONE }) {
                    TextButton(onClick = { Engine.clearDone() }) {
                        Text("Убрать готовые", color = P.MUTED)
                    }
                }
            }
            if (items.isEmpty()) {
                Text("ОЧЕРЕДЬ ПУСТА\nВставьте ссылку или нажмите «Поделиться» → Downloader " +
                        "в YouTube, VK, TikTok…", color = P.MUTED, fontSize = 13.sp,
                    modifier = Modifier.fillMaxWidth().padding(24.dp))
            }
        }
        items(items, key = { it.id }) { ItemCard(it) }
        item { Spacer(Modifier.height(16.dp)) }
    }
}

/** Видео/аудио, качество, формат, плейлист, без рекламы. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun KindPicker(opts: Opts, onChange: (Opts) -> Unit) {
    Column {
        Row(Modifier.padding(top = 10.dp)) {
            PChip("🎬  ВИДЕО", !opts.isAudio) { onChange(opts.withKind(mode = "video")) }
            PChip("🎵  АУДИО", opts.isAudio) { onChange(opts.withKind(mode = "audio")) }
        }
        PLabel("Качество")
        Row(Modifier.horizontalScroll(rememberScrollState())) {
            if (opts.isAudio) {
                Opts.AUDIO_QUALITIES.forEach { q ->
                    PChip(Opts.audioQualityLabel(q), opts.quality == q) { onChange(opts.copy(quality = q)) }
                }
            } else {
                Opts.VIDEO_QUALITIES.forEach { q ->
                    PChip(Opts.qualityLabel(q), opts.quality == q) { onChange(opts.copy(quality = q)) }
                }
            }
        }
        PLabel("Формат")
        FlowRow {
            (if (opts.isAudio) Opts.AUDIO_FORMATS else Opts.VIDEO_FORMATS).forEach { f ->
                PChip(f.uppercase(), opts.fmt == f) { onChange(opts.copy(fmt = f)) }
            }
        }
        FlowRow(Modifier.padding(top = 4.dp)) {
            PChip((if (opts.playlist) "✓ " else "") + "Весь плейлист", opts.playlist) {
                onChange(opts.copy(playlist = !opts.playlist))
            }
            PChip((if (opts.noAds) "✓ " else "") + "Без рекламы", opts.noAds) {
                onChange(opts.copy(noAds = !opts.noAds))
            }
        }
    }
}

/** Окно «Скачать как…» — тот же выбор, что сверху, но для одной ссылки. */
@Composable
fun KindDialog(title: String, start: Opts, onDismiss: () -> Unit, onPick: (Opts) -> Unit) {
    var o by remember { mutableStateOf(start) }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = P.CARD,
        title = { Text(title.uppercase(), color = P.WHITE, style = Heavy, fontSize = 18.sp) },
        text = { Column(Modifier.verticalScroll(rememberScrollState())) { KindPicker(o) { o = it } } },
        confirmButton = { PButton("Скачать", height = 38.dp) { onPick(o); onDismiss() } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена", color = P.MUTED) } },
    )
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun ItemCard(item: Item) {
    val ctx = LocalContext.current
    var menu by remember { mutableStateOf(false) }
    var kind by remember { mutableStateOf<String?>(null) }   // "change" | "again"
    val stripe = when (item.state) {
        State.DONE -> P.GREEN
        State.ERROR -> P.RED
        State.RUN -> P.RED
        else -> P.CARD2
    }
    Box(Modifier.padding(bottom = 8.dp)) {
        PCard(stripe = stripe) {
            Row(
                Modifier.fillMaxWidth()
                    .combinedClickable(
                        onClick = { if (item.state == State.DONE) item.files.firstOrNull()?.let { openFile(ctx, it) } else menu = true },
                        onLongClick = { menu = true })
                    .padding(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(Modifier.width(112.dp).aspectRatio(16f / 9f).background(P.RED),
                    contentAlignment = Alignment.Center) {
                    Text(if (item.opts.isAudio) item.opts.fmt.uppercase()
                         else "${Opts.qualityLabel(item.opts.quality)}\n${item.opts.fmt.uppercase()}",
                        color = P.WHITE, style = Heavy, fontSize = 13.sp)
                    if (item.thumb != null) {
                        AsyncImage(item.thumb, null, Modifier.fillMaxSize(),
                            contentScale = ContentScale.Crop)
                    }
                }
                Column(Modifier.weight(1f).padding(start = 10.dp)) {
                    Text(item.label, color = P.WHITE, fontWeight = FontWeight.Bold, fontSize = 14.sp,
                        maxLines = 2, overflow = TextOverflow.Ellipsis)
                    if (item.meta.isNotEmpty()) {
                        Text(item.meta, color = P.MUTED, fontSize = 12.sp, maxLines = 2,
                            overflow = TextOverflow.Ellipsis)
                    }
                    if (item.state == State.RUN) {
                        LinearProgressIndicator(progress = { item.progress.coerceIn(0f, 1f) },
                            modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp).height(4.dp),
                            color = P.RED, trackColor = P.CARD2)
                    }
                    Text(item.status, fontSize = 12.sp, maxLines = 2, overflow = TextOverflow.Ellipsis,
                        color = when (item.state) {
                            State.ERROR -> P.RED; State.DONE -> P.GREEN; else -> P.MUTED
                        })
                }
                val (icon, action) = when (item.state) {
                    State.RUN -> "■" to { Engine.stop(item.id) }
                    State.WAIT -> "✕" to { Engine.remove(item.id) }
                    State.ERROR, State.STOPPED -> "⟳" to { Engine.retry(item.id) }
                    State.DONE -> "▶" to { item.files.firstOrNull()?.let { openFile(ctx, it) }; Unit }
                }
                Text(icon, color = P.WHITE, fontSize = 20.sp,
                    modifier = Modifier.clickable { action() }.padding(10.dp))
            }
        }
        DropdownMenu(menu, { menu = false }, Modifier.background(P.CARD)) {
            if (item.state != State.RUN && item.state != State.DONE) {
                DropdownMenuItem(text = { Text("Сейчас: ${item.opts.describe()}", color = P.MUTED) },
                    onClick = {}, enabled = false)
                MenuItem("🎚  Скачать как…") { menu = false; kind = "change" }
            }
            if (item.state == State.RUN) MenuItem("■  Остановить") { menu = false; Engine.stop(item.id) }
            if (item.state == State.ERROR || item.state == State.STOPPED) {
                MenuItem("⟳  Повторить") { menu = false; Engine.retry(item.id) }
            }
            if (item.state == State.DONE) {
                item.files.firstOrNull()?.let { f ->
                    MenuItem("▶  Открыть") { menu = false; openFile(ctx, f) }
                    MenuItem("↗  Поделиться") { menu = false; shareFile(ctx, f) }
                }
                MenuItem("⟳  Скачать снова как…") { menu = false; kind = "again" }
            }
            MenuItem("🔗  Копировать ссылку") { menu = false; copyText(ctx, item.url) }
            MenuItem("Убрать из списка") { menu = false; Engine.remove(item.id) }
        }
    }
    kind?.let { k ->
        KindDialog(if (k == "change") "Скачать как" else "Скачать снова как", item.opts,
            onDismiss = { kind = null }) { o ->
            if (k == "change") Engine.changeKind(item.id, o) else Engine.redownload(item.id, o)
        }
    }
}

// =================================================================== галерея
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun GalleryScreen() {
    val history by Engine.history.collectAsState()
    var filter by remember { mutableStateOf("all") }
    var query by remember { mutableStateOf("") }
    var tick by remember { mutableStateOf(0) }       // перерисовать после удаления
    val files = remember(history, filter, query, tick) {
        val seen = HashSet<String>()
        history.filter { r ->
            val f = File(r.file)
            val audio = r.file.substringAfterLast('.').lowercase() in AUDIO_EXT
            f.isFile && seen.add(r.file) &&
                (filter == "all" || (filter == "audio") == audio) &&
                (query.isBlank() || r.title.contains(query, true) || f.name.contains(query, true))
        }
    }
    Column(Modifier.fillMaxSize().padding(horizontal = 12.dp)) {
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            PTitle(R.drawable.t_gallery)
            Spacer(Modifier.weight(1f))
            Text("Файлов: ${files.size}  •  ${humanSize(files.sumOf { File(it.file).length() }) ?: "0 Б"}",
                color = P.MUTED, fontSize = 12.sp)
        }
        PField(query, { query = it }, "Поиск по названию", Modifier.fillMaxWidth().padding(top = 6.dp))
        Row(Modifier.padding(top = 8.dp)) {
            PChip("Все", filter == "all") { filter = "all" }
            PChip("🎬 Видео", filter == "video") { filter = "video" }
            PChip("🎵 Музыка", filter == "audio") { filter = "audio" }
        }
        if (files.isEmpty()) {
            Text(if (history.isEmpty()) "Здесь появятся скачанные видео и музыка" else "Ничего не найдено",
                color = P.MUTED, modifier = Modifier.fillMaxWidth().padding(30.dp))
        }
        LazyVerticalGrid(GridCells.Adaptive(160.dp), Modifier.fillMaxSize(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(files, key = { it.file }) { r -> Tile(r) { tick++ } }
        }
    }
}

val AUDIO_EXT = setOf("mp3", "m4a", "opus", "flac", "ogg", "wav", "aac")

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun Tile(r: Rec, onChanged: () -> Unit) {
    val ctx = LocalContext.current
    val f = File(r.file)
    val ext = f.extension.lowercase()
    val audio = ext in AUDIO_EXT
    var menu by remember { mutableStateOf(false) }
    var again by remember { mutableStateOf(false) }
    var confirm by remember { mutableStateOf(false) }
    Box {
        Column(Modifier.background(P.CARD)
            .combinedClickable(onClick = { openFile(ctx, r.file) }, onLongClick = { menu = true })) {
            Box(Modifier.fillMaxWidth().aspectRatio(16f / 9f).background(P.RED),
                contentAlignment = Alignment.Center) {
                Text((if (audio) "♪\n" else "▶\n") + ext.uppercase(), color = P.WHITE, style = Heavy,
                    fontSize = 18.sp)
                // кадр из самого видео; у музыки — обложка с сайта
                val model: Any? = if (audio) r.thumb else f
                if (model != null) {
                    AsyncImage(model, null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
                }
                humanDuration(r.duration)?.let {
                    Text(it, color = P.WHITE, fontSize = 11.sp, fontWeight = FontWeight.Bold,
                        modifier = Modifier.align(Alignment.BottomEnd).padding(4.dp)
                            .background(P.RED).padding(horizontal = 5.dp, vertical = 1.dp))
                }
            }
            Text(r.title, color = P.WHITE, fontWeight = FontWeight.Bold, fontSize = 13.sp, maxLines = 2,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(start = 8.dp, end = 8.dp, top = 6.dp))
            Text(listOfNotNull(ext.uppercase(), humanSize(f.length()),
                SimpleDateFormat("dd.MM.yyyy", Locale.ROOT).format(Date(r.date))).joinToString("  •  "),
                color = P.MUTED, fontSize = 11.sp, modifier = Modifier.padding(8.dp))
        }
        DropdownMenu(menu, { menu = false }, Modifier.background(P.CARD)) {
            DropdownMenuItem(text = { Text("▶  Открыть", color = P.WHITE) },
                onClick = { menu = false; openFile(ctx, r.file) })
            DropdownMenuItem(text = { Text("↗  Поделиться", color = P.WHITE) },
                onClick = { menu = false; shareFile(ctx, r.file) })
            if (r.url.isNotEmpty()) {
                DropdownMenuItem(text = { Text("⟳  Скачать снова как…", color = P.WHITE) },
                    onClick = { menu = false; again = true })
                DropdownMenuItem(text = { Text("🔗  Копировать ссылку", color = P.WHITE) },
                    onClick = { menu = false; copyText(ctx, r.url) })
            }
            DropdownMenuItem(text = { Text("🗑  Удалить файл", color = P.RED) },
                onClick = { menu = false; confirm = true })
        }
    }
    if (again) {
        KindDialog("Скачать снова как", r.opts, onDismiss = { again = false }) { o ->
            Engine.redownload(r, o)
            Engine.message.value = "Добавлено в очередь"
        }
    }
    if (confirm) {
        AlertDialog(onDismissRequest = { confirm = false }, containerColor = P.CARD,
            title = { Text("УДАЛИТЬ ФАЙЛ?", color = P.WHITE, style = Heavy) },
            text = { Text(f.name, color = P.MUTED) },
            confirmButton = {
                PButton("Удалить", height = 38.dp) {
                    confirm = false
                    if (Engine.deleteFile(r.file)) onChanged()
                    else Engine.message.value = "Не удалось удалить файл"
                }
            },
            dismissButton = { TextButton(onClick = { confirm = false }) { Text("Отмена", color = P.MUTED) } })
    }
}

// =================================================================== настройки
@Composable
fun SettingsScreen() {
    val ctx = LocalContext.current
    val s by Engine.settings.collectAsState()
    val version by Engine.ytdlpVersion.collectAsState()
    val updating by Engine.updating.collectAsState()
    val log by Engine.log.collectAsState()
    var proxy by remember(s.proxy) { mutableStateOf(s.proxy) }
    var showLog by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 12.dp)) {
        PTitle(R.drawable.t_settings, Modifier.padding(top = 8.dp))

        PLabel("Одновременно загрузок")
        Row { (1..3).forEach { n -> PChip("$n", s.parallel == n) { Engine.saveSettings(s.copy(parallel = n)) } } }

        PLabel("Звук «ГОТОВО!»")
        Row(verticalAlignment = Alignment.CenterVertically) {
            Switch(s.sound, { Engine.saveSettings(s.copy(sound = it)) },
                colors = SwitchDefaults.colors(checkedTrackColor = P.RED, checkedThumbColor = P.WHITE))
            Text("  Звук из Persona 5, когда всё скачано", color = P.WHITE, fontSize = 13.sp)
        }

        PLabel("Прокси")
        PField(proxy, { proxy = it }, "socks5://127.0.0.1:10808", Modifier.fillMaxWidth(),
            onDone = { Engine.saveSettings(s.copy(proxy = proxy.trim())) })
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 6.dp)) {
            PButton("Сохранить", primary = false, height = 36.dp,
                enabled = proxy.trim() != s.proxy) {
                Engine.saveSettings(s.copy(proxy = proxy.trim()))
                Engine.message.value = if (proxy.isBlank()) "Прокси выключен" else "Прокси сохранён"
            }
        }
        Text("Обычно не нужен: VPN-приложение на телефоне и так работает для всех программ. " +
                "Нужен, только если в VPN-приложении включён режим «только прокси».",
            color = P.MUTED, fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp))

        PLabel("yt-dlp")
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Версия: ${version ?: "…"}", color = P.WHITE, fontSize = 14.sp,
                modifier = Modifier.weight(1f))
            if (updating) CircularProgressIndicator(Modifier.size(20.dp), color = P.RED, strokeWidth = 2.dp)
            else PButton("Обновить", height = 36.dp) {
                Engine.scope.launch { Engine.updateYtdlp(manual = true) }
            }
        }
        Text("Обновляется сам раз в день — YouTube не «ломается» из-за старой версии.",
            color = P.MUTED, fontSize = 12.sp)

        PLabel("Папка")
        Text("Загрузки/Downloader", color = P.WHITE, fontSize = 14.sp)

        PLabel("Журнал")
        TextButton(onClick = { showLog = !showLog }) {
            Text(if (showLog) "Скрыть" else "Показать (${log.size})", color = P.RED)
        }
        if (showLog) {
            Column(Modifier.fillMaxWidth().background(Color.Black).padding(8.dp)) {
                log.takeLast(150).forEach {
                    Text(it, color = P.MUTED, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                }
            }
            TextButton(onClick = {
                copyText(ctx, log.joinToString("\n"))
            }) { Text("Копировать журнал", color = P.RED) }
        }
        Text("Downloader для Android ${BuildConfig.VERSION_NAME} (сборка ${BuildConfig.VERSION_CODE})",
            color = P.MUTED, fontSize = 12.sp, modifier = Modifier.padding(vertical = 16.dp))
    }
}

@Composable
fun MenuItem(text: String, onClick: () -> Unit) {
    DropdownMenuItem(text = { Text(text, color = P.WHITE) }, onClick = onClick)
}

// =================================================================== файлы
fun mimeOf(path: String): String =
    MimeTypeMap.getSingleton().getMimeTypeFromExtension(path.substringAfterLast('.').lowercase())
        ?: if (path.substringAfterLast('.').lowercase() in AUDIO_EXT) "audio/*" else "video/*"

fun uriOf(ctx: Context, path: String) =
    FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", File(path))

fun openFile(ctx: Context, path: String) {
    if (!File(path).exists()) { Engine.message.value = "Файла больше нет"; return }
    runCatching {
        ctx.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uriOf(ctx, path), mimeOf(path))
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
    }.onFailure { Engine.message.value = "Нет приложения, чтобы открыть этот файл" }
}

fun shareFile(ctx: Context, path: String) {
    runCatching {
        val send = Intent(Intent.ACTION_SEND).setType(mimeOf(path))
            .putExtra(Intent.EXTRA_STREAM, uriOf(ctx, path))
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        ctx.startActivity(Intent.createChooser(send, "Поделиться").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

fun copyText(ctx: Context, text: String) {
    val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    cm.setPrimaryClip(ClipData.newPlainText("Downloader", text))
    Engine.message.value = "Скопировано"
}
