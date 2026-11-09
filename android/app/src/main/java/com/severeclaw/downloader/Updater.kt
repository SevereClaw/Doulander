package com.severeclaw.downloader

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings as AndroidSettings
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import org.json.JSONArray
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/** Обновление самого приложения из релизов GitHub (теги android-v1.2.0-<сборка>). */
object Updater {
    const val REPO = "SevereClaw/Doulander"

    /** Ссылка на создание токена: имя, срок «без окончания» и право «только чтение» уже заполнены. */
    const val TOKEN_URL = "https://github.com/settings/personal-access-tokens/new" +
        "?name=Downloader+Android&description=%D0%9E%D0%B1%D0%BD%D0%BE%D0%B2%D0%BB%D0%B5%D0%BD%D0%B8%D1%8F+Downloader" +
        "&target_name=SevereClaw&expires_in=none&contents=read"

    data class Release(val tag: String, val build: Int, val version: String, val notes: String,
                       val assetUrl: String, val assetName: String, val size: Long)

    sealed class St {
        object Idle : St()
        object NoToken : St()
        object Checking : St()
        object UpToDate : St()
        data class Downloading(val r: Release, val progress: Float) : St()
        data class Ready(val r: Release, val file: File) : St()
        data class Installing(val r: Release) : St()
        data class Error(val text: String) : St()
    }

    val state = MutableStateFlow<St>(St.Idle)
    private var lastCheck = 0L
    private var promptedBuild = 0

    /** Автоустановка, когда приложение на экране: спрашиваем один раз за запуск. */
    fun autoInstall(c: Context) {
        val st = state.value as? St.Ready ?: return
        if (!Engine.settings.value.autoUpdate || promptedBuild == st.r.build) return
        promptedBuild = st.r.build
        install(c)
    }

    /** «android-v1.2.0-15» → 15 (номер сборки = versionCode). */
    fun buildOf(tag: String): Int? = Regex("""^android-v[\d.]+-(\d+)$""").find(tag)?.groupValues?.get(1)?.toIntOrNull()

    /** Самый новый релиз для Android с APK под этот процессор. */
    fun pick(json: String, abis: List<String>): Release? {
        val a = JSONArray(json)
        var best: Release? = null
        for (i in 0 until a.length()) {
            val r = a.getJSONObject(i)
            if (r.optBoolean("draft")) continue
            val tag = r.optString("tag_name")
            val build = buildOf(tag) ?: continue
            val assets = r.optJSONArray("assets") ?: continue
            val names = List(assets.length()) { assets.getJSONObject(it) }
            // arm64 подходит почти всем телефонам; armv7 — старым; x86_64 — эмулятору
            val wanted = abis.mapNotNull { abi ->
                when (abi) {
                    "arm64-v8a" -> "Downloader-arm64.apk"
                    "armeabi-v7a" -> "Downloader-armv7.apk"
                    "x86_64" -> "Downloader-x86_64.apk"
                    else -> null
                }
            }
            val asset = wanted.firstNotNullOfOrNull { w -> names.firstOrNull { it.optString("name") == w } }
                ?: continue
            if (best == null || build > best.build) {
                best = Release(tag, build, tag.removePrefix("android-v").substringBeforeLast('-'),
                    r.optString("body"), asset.optString("url"), asset.optString("name"),
                    asset.optLong("size"))
            }
        }
        return best
    }

    private fun open(url: String, token: String?, accept: String): HttpURLConnection {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 20000
        c.readTimeout = 30000
        c.instanceFollowRedirects = false
        c.setRequestProperty("Accept", accept)
        c.setRequestProperty("User-Agent", "Downloader-Android")
        if (token != null) {
            c.setRequestProperty("Authorization", "Bearer $token")
            c.setRequestProperty("X-GitHub-Api-Version", "2022-11-28")
        }
        return c
    }

    /** Проверить и, если включено, сразу скачать и поставить. */
    suspend fun check(manual: Boolean) {
        val s = Engine.settings.value
        val token = s.githubToken.trim()
        if (token.isEmpty()) { state.value = St.NoToken; return }
        if (state.value is St.Downloading || state.value is St.Installing) return
        if (!manual && System.currentTimeMillis() - lastCheck < 3 * 3600 * 1000L &&
            state.value !is St.Error) return
        lastCheck = System.currentTimeMillis()
        state.value = St.Checking
        try {
            val c = open("https://api.github.com/repos/$REPO/releases?per_page=30", token,
                "application/vnd.github+json")
            val code = c.responseCode
            if (code == 401 || code == 403 || code == 404) {
                state.value = St.Error(if (code == 401) "Токен не подходит или истёк — создайте новый"
                    else "У токена нет доступа к репозиторию Doulander")
                return
            }
            if (code != 200) { state.value = St.Error("GitHub ответил: $code"); return }
            val json = c.inputStream.bufferedReader().readText()
            val r = pick(json, Build.SUPPORTED_ABIS.toList())
            if (r == null || r.build <= BuildConfig.VERSION_CODE) {
                state.value = St.UpToDate
                if (manual) Engine.message.value = "У вас последняя версия"
                return
            }
            Engine.log("Вышла новая версия ${r.version} (сборка ${r.build})")
            if (manual || s.autoUpdate) download(r, token) else state.value = St.Idle
        } catch (e: Throwable) {
            state.value = St.Error("Нет связи с GitHub: ${e.message}")
        }
    }

    private suspend fun download(r: Release, token: String) {
        val dir = File(Engine.app.cacheDir, "update").apply { deleteRecursively(); mkdirs() }
        val out = File(dir, "Downloader-${r.build}.apk")
        state.value = St.Downloading(r, 0f)
        try {
            // GitHub отдаёт файл через переадресацию на хранилище — туда токен не передаём
            var c = open(r.assetUrl, token, "application/octet-stream")
            var hops = 0
            while (c.responseCode in 301..308 && hops < 5) {
                val loc = c.getHeaderField("Location")
                c.disconnect()
                c = open(loc, null, "application/octet-stream")
                hops++
            }
            if (c.responseCode != 200) { state.value = St.Error("Не удалось скачать: ${c.responseCode}"); return }
            val total = c.contentLengthLong.takeIf { it > 0 } ?: r.size
            c.inputStream.use { inp ->
                out.outputStream().use { o ->
                    val buf = ByteArray(64 * 1024)
                    var done = 0L
                    var last = 0L
                    while (true) {
                        val n = inp.read(buf)
                        if (n < 0) break
                        o.write(buf, 0, n)
                        done += n
                        if (done - last > 512 * 1024) {
                            last = done
                            state.value = St.Downloading(r, if (total > 0) done.toFloat() / total else 0f)
                        }
                    }
                }
            }
            state.value = St.Ready(r, out)
            Engine.log("Новая версия скачана: ${out.name}")
            DownloadService.notifyUpdate(Engine.app, r.version)
        } catch (e: Throwable) {
            state.value = St.Error("Обновление не скачалось: ${e.message}")
        }
    }

    /** Можно ли ставить APK; если нет — открыть настройку «Установка неизвестных приложений». */
    fun canInstall(c: Context): Boolean {
        if (Build.VERSION.SDK_INT < 26 || c.packageManager.canRequestPackageInstalls()) return true
        c.startActivity(Intent(AndroidSettings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
            Uri.parse("package:${c.packageName}")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        Engine.message.value = "Разрешите Downloader устанавливать обновления и нажмите «Установить» ещё раз"
        return false
    }

    /** Установка через системный установщик. На Android 12+ после первого раза — без лишних вопросов. */
    fun install(c: Context) {
        val st = state.value as? St.Ready ?: return
        if (!canInstall(c)) return
        try {
            val pi = c.packageManager.packageInstaller
            val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
            if (Build.VERSION.SDK_INT >= 31) {
                params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
            val id = pi.createSession(params)
            pi.openSession(id).use { s ->
                s.openWrite("Downloader.apk", 0, st.file.length()).use { o ->
                    st.file.inputStream().use { it.copyTo(o) }
                    s.fsync(o)
                }
                val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                    if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
                val pending = PendingIntent.getBroadcast(c, id,
                    Intent(c, InstallReceiver::class.java), flags)
                state.value = St.Installing(st.r)
                s.commit(pending.intentSender)
            }
        } catch (e: Throwable) {
            state.value = St.Error("Не удалось установить: ${e.message}")
        }
    }

    /** Проверка при запуске и потом раз в 3 часа, пока приложение открыто. */
    fun startAutoCheck() {
        Engine.scope.launch {
            delay(5000)
            while (true) {
                if (Engine.settings.value.autoUpdate) check(manual = false)
                delay(3 * 3600 * 1000L)
            }
        }
    }
}

/** Ответ системного установщика: спросить подтверждение или сообщить об ошибке. */
class InstallReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        when (val status = i.getIntExtra(PackageInstaller.EXTRA_STATUS, -999)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = if (Build.VERSION.SDK_INT >= 33)
                    i.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                else i.getParcelableExtra(Intent.EXTRA_INTENT)
                confirm?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)?.let { runCatching { c.startActivity(it) } }
            }
            PackageInstaller.STATUS_SUCCESS -> Engine.log("Обновление установлено")
            else -> {
                val msg = i.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "код $status"
                Engine.log("Обновление не установилось: $msg")
                val ready = Updater.state.value
                if (ready is Updater.St.Installing) {
                    val f = File(Engine.app.cacheDir, "update/Downloader-${ready.r.build}.apk")
                    Updater.state.value = if (status == PackageInstaller.STATUS_FAILURE_ABORTED && f.exists())
                        Updater.St.Ready(ready.r, f) else Updater.St.Error("Не установилось: $msg")
                }
            }
        }
    }
}
