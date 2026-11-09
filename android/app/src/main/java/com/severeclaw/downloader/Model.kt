package com.severeclaw.downloader

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Как скачивать: видео или звук, качество, формат. */
data class Opts(
    val mode: String = "video",          // "video" | "audio"
    val quality: String = "1080",        // best, 2160, 1440, 1080, 720, 480, 360 (для аудио: best, 320, 192, 128)
    val fmt: String = "mp4",             // mp4 mkv webm | mp3 m4a opus flac
    val playlist: Boolean = false,
    val noAds: Boolean = false,
) {
    val isAudio get() = mode == "audio"

    fun toJson() = JSONObject().put("mode", mode).put("quality", quality).put("fmt", fmt)
        .put("playlist", playlist).put("noAds", noAds)

    /** «Видео 1080p MP4» / «Аудио MP3» */
    fun describe(): String = if (isAudio) {
        "Аудио ${fmt.uppercase()}" + if (quality == "best") "" else " ${quality}k"
    } else {
        "Видео ${qualityLabel(quality)} ${fmt.uppercase()}"
    }

    /** Другой вид загрузки с подходящими качеством и форматом. */
    fun withKind(mode: String? = null, quality: String? = null, fmt: String? = null): Opts {
        var o = copy(mode = mode ?: this.mode, quality = quality ?: this.quality, fmt = fmt ?: this.fmt)
        if (o.isAudio) {
            if (o.fmt !in AUDIO_FORMATS) o = o.copy(fmt = "mp3")
            if (o.quality !in AUDIO_QUALITIES) o = o.copy(quality = "best")
        } else {
            if (o.fmt !in VIDEO_FORMATS) o = o.copy(fmt = "mp4")
            if (o.quality !in VIDEO_QUALITIES) o = o.copy(quality = "1080")
        }
        return o
    }

    companion object {
        val VIDEO_QUALITIES = listOf("best", "2160", "1440", "1080", "720", "480", "360")
        val AUDIO_QUALITIES = listOf("best", "320", "192", "128")
        val VIDEO_FORMATS = listOf("mp4", "mkv", "webm")
        val AUDIO_FORMATS = listOf("mp3", "m4a", "opus", "flac")

        fun qualityLabel(q: String) = if (q == "best") "Лучшее" else "${q}p"
        fun audioQualityLabel(q: String) = if (q == "best") "Лучшее" else "$q kbps"

        fun fromJson(j: JSONObject?): Opts = if (j == null) Opts() else Opts(
            mode = j.optString("mode", "video"),
            quality = j.optString("quality", "1080"),
            fmt = j.optString("fmt", "mp4"),
            playlist = j.optBoolean("playlist", false),
            noAds = j.optBoolean("noAds", false),
        ).withKind()
    }
}

enum class State { WAIT, RUN, PAUSED, DONE, ERROR, STOPPED }

/** Ссылка в очереди. */
data class Item(
    val id: String,
    val url: String,
    val opts: Opts,
    val title: String? = null,
    val thumb: String? = null,
    val meta: String = "",
    val state: State = State.WAIT,
    val progress: Float = 0f,
    val status: String = "В очереди",
    val files: List<String> = emptyList(),
    val pcId: String? = null,           // файл с ПК (забирается по Wi-Fi, а не с сайта)
    val pcName: String? = null,
) {
    val label get() = title ?: url

    fun toJson() = JSONObject().put("id", id).put("url", url).put("opts", opts.toJson())
        .put("title", title).put("thumb", thumb).put("meta", meta).put("state", state.name)
        .put("progress", progress.toDouble()).put("status", status)
        .put("files", JSONArray(files)).put("pcId", pcId).put("pcName", pcName)

    companion object {
        fun fromJson(j: JSONObject) = Item(
            id = j.getString("id"),
            url = j.getString("url"),
            opts = Opts.fromJson(j.optJSONObject("opts")),
            title = j.optString("title").ifEmpty { null }.takeIf { it != "null" },
            thumb = j.optString("thumb").ifEmpty { null }.takeIf { it != "null" },
            meta = j.optString("meta"),
            state = runCatching { State.valueOf(j.optString("state")) }.getOrDefault(State.WAIT),
            progress = j.optDouble("progress", 0.0).toFloat(),
            status = j.optString("status"),
            files = j.optJSONArray("files")?.let { a -> List(a.length()) { a.getString(it) } }
                ?: emptyList(),
            pcId = j.optString("pcId").ifEmpty { null }.takeIf { it != "null" },
            pcName = j.optString("pcName").ifEmpty { null }.takeIf { it != "null" },
        )
    }
}

/** Запись истории: что и куда скачано. */
data class Rec(
    val url: String,
    val title: String,
    val file: String,
    val thumb: String?,
    val date: Long,
    val opts: Opts,
    val duration: Int? = null,
) {
    fun toJson() = JSONObject().put("url", url).put("title", title).put("file", file)
        .put("thumb", thumb).put("date", date).put("opts", opts.toJson()).put("duration", duration)

    companion object {
        fun fromJson(j: JSONObject) = Rec(
            url = j.optString("url"),
            title = j.optString("title"),
            file = j.optString("file"),
            thumb = j.optString("thumb").ifEmpty { null }.takeIf { it != "null" },
            date = j.optLong("date"),
            opts = Opts.fromJson(j.optJSONObject("opts")),
            duration = if (j.has("duration") && !j.isNull("duration")) j.optInt("duration") else null,
        )
    }
}

/** Настройки программы. */
data class Settings(
    val opts: Opts = Opts(),
    val proxy: String = "",
    val parallel: Int = 2,
    val sound: Boolean = true,
    val lastYtdlpUpdate: Long = 0,
    val githubToken: String = "",        // токен только для чтения — чтобы видеть релизы закрытого репозитория
    val autoUpdate: Boolean = true,
    val deviceId: String = "",           // код этого телефона для ПК
    val syncHost: String = "",           // адрес ПК, например 192.168.1.5:47614
    val syncKey: String = "",
    val syncName: String = "",           // имя ПК
) {
    fun toJson() = JSONObject().put("opts", opts.toJson()).put("proxy", proxy)
        .put("parallel", parallel).put("sound", sound).put("lastYtdlpUpdate", lastYtdlpUpdate)
        .put("githubToken", githubToken).put("autoUpdate", autoUpdate)
        .put("deviceId", deviceId).put("syncHost", syncHost).put("syncKey", syncKey)
        .put("syncName", syncName)

    companion object {
        fun fromJson(j: JSONObject?) = if (j == null) Settings() else Settings(
            opts = Opts.fromJson(j.optJSONObject("opts")),
            proxy = j.optString("proxy"),
            parallel = j.optInt("parallel", 2).coerceIn(1, 3),
            sound = j.optBoolean("sound", true),
            lastYtdlpUpdate = j.optLong("lastYtdlpUpdate"),
            githubToken = j.optString("githubToken"),
            autoUpdate = j.optBoolean("autoUpdate", true),
            deviceId = j.optString("deviceId"),
            syncHost = j.optString("syncHost"),
            syncKey = j.optString("syncKey"),
            syncName = j.optString("syncName"),
        )
    }
}

/** Файлы настроек, истории и очереди — во внутренней папке приложения. */
object Store {
    private fun file(c: Context, name: String) = File(c.filesDir, name)

    /** Запись через временный файл — при обрыве старый файл остаётся целым. */
    private fun write(c: Context, name: String, text: String) {
        val f = file(c, name)
        val tmp = File(f.path + ".tmp")
        runCatching {
            tmp.writeText(text)
            if (!tmp.renameTo(f)) {
                f.delete(); tmp.renameTo(f)
            }
        }
    }

    private fun read(c: Context, name: String): String? =
        runCatching { file(c, name).takeIf { it.exists() }?.readText() }.getOrNull()

    fun loadSettings(c: Context) =
        Settings.fromJson(read(c, "settings.json")?.let { runCatching { JSONObject(it) }.getOrNull() })

    fun saveSettings(c: Context, s: Settings) = write(c, "settings.json", s.toJson().toString(1))

    fun loadHistory(c: Context): List<Rec> = read(c, "history.json")?.let { t ->
        runCatching {
            val a = JSONArray(t); List(a.length()) { Rec.fromJson(a.getJSONObject(it)) }
        }.getOrNull()
    } ?: emptyList()

    fun saveHistory(c: Context, h: List<Rec>) =
        write(c, "history.json", JSONArray(h.take(2000).map { it.toJson() }).toString(1))

    fun loadQueue(c: Context): List<Item> = read(c, "queue.json")?.let { t ->
        runCatching {
            val a = JSONArray(t); List(a.length()) { Item.fromJson(a.getJSONObject(it)) }
        }.getOrNull()
    } ?: emptyList()

    fun saveQueue(c: Context, q: List<Item>) =
        write(c, "queue.json", JSONArray(q.map { it.toJson() }).toString(1))
}

fun humanSize(bytes: Long?): String? {
    if (bytes == null || bytes <= 0) return null
    val units = listOf("Б", "КБ", "МБ", "ГБ")
    var v = bytes.toDouble()
    var i = 0
    while (v >= 1024 && i < units.size - 1) { v /= 1024; i++ }
    return if (i == 0) "$bytes Б" else String.format("%.1f %s", v, units[i])
}

fun humanDuration(sec: Int?): String? {
    if (sec == null || sec <= 0) return null
    val h = sec / 3600; val m = sec % 3600 / 60; val s = sec % 60
    return if (h > 0) String.format("%d:%02d:%02d", h, m, s) else String.format("%d:%02d", m, s)
}
