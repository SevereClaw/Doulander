package com.severeclaw.downloader

import android.annotation.SuppressLint
import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.webkit.WebSettingsCompat
import androidx.webkit.WebViewFeature
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Cookies аккаунта YouTube для yt-dlp: «вы не бот?», видео 18+, закрытые для гостей видео. */
object YouTubeCookies {
    val file: File get() = File(Engine.app.filesDir, "youtube-cookies.txt")
    val present: Boolean get() = file.isFile && file.length() > 0

    /** Вошёл ли пользователь: у YouTube есть cookies аккаунта Google. */
    fun loggedIn(cookies: String?): Boolean =
        cookies != null && ("SAPISID=" in cookies || "__Secure-3PSID=" in cookies)

    /** «a=1; b=2» (как отдаёт WebView) → файл cookies в формате Netscape, который понимает yt-dlp. */
    fun toNetscape(cookies: String, now: Long = System.currentTimeMillis() / 1000): String {
        val expires = now + 365L * 24 * 3600
        val sb = StringBuilder("# Netscape HTTP Cookie File\n# Downloader для Android\n\n")
        for (part in cookies.split(';')) {
            val p = part.trim()
            val eq = p.indexOf('=')
            if (eq <= 0) continue
            val name = p.substring(0, eq).trim()
            val value = p.substring(eq + 1).trim()
            // cookies «__Host-» привязаны к одному адресу, остальные — ко всему youtube.com
            val (domain, sub) = if (name.startsWith("__Host-")) "www.youtube.com" to "FALSE"
                else ".youtube.com" to "TRUE"
            sb.append("$domain\t$sub\t/\tTRUE\t$expires\t$name\t$value\n")
        }
        return sb.toString()
    }

    fun save(cookies: String) {
        file.writeText(toNetscape(cookies))
        Engine.log("Вход в YouTube сохранён")
    }

    fun logout() {
        file.delete()
        CookieManager.getInstance().removeAllCookies(null)
        Engine.log("Выход из YouTube")
    }

    fun savedAt(): String? = if (present)
        SimpleDateFormat("dd.MM.yyyy", Locale.ROOT).format(Date(file.lastModified())) else null
}

/** Окно входа в YouTube (страница Google внутри приложения). */
class LoginActivity : ComponentActivity() {
    private var web: WebView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // чистый вход: старая сессия окна не мешает
        CookieManager.getInstance().removeAllCookies(null)
        setContent { LoginScreen() }
    }

    override fun onDestroy() {
        web?.destroy()
        super.onDestroy()
    }

    private fun finishWith(cookies: String) {
        YouTubeCookies.save(cookies)
        // окно больше не пользуется этой сессией — иначе YouTube «прокрутит» cookies
        // и сохранённые перестанут работать
        CookieManager.getInstance().removeAllCookies(null)
        CookieManager.getInstance().flush()
        Engine.message.value = "Вход в YouTube выполнен — загрузки пойдут от вашего аккаунта"
        Engine.retryLoginErrors()
        finish()
    }

    @SuppressLint("SetJavaScriptEnabled")
    @Composable
    private fun LoginScreen() {
        var logged by remember { mutableStateOf(false) }
        Column(Modifier.fillMaxSize().background(P.BG)) {
            Row(Modifier.fillMaxWidth().padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("ВХОД В YOUTUBE", color = P.WHITE, style = Heavy, fontSize = 18.sp)
                    Text(if (logged) "Готово! Нажмите «Сохранить»"
                         else "Войдите в аккаунт Google — пароль видит только Google",
                        color = if (logged) P.GREEN else P.MUTED, fontSize = 12.sp)
                }
                Spacer(Modifier.width(8.dp))
                PButton("Сохранить", enabled = logged, height = 38.dp) {
                    CookieManager.getInstance().getCookie("https://www.youtube.com")?.let { finishWith(it) }
                }
                Spacer(Modifier.width(6.dp))
                PButton("✕", primary = false, height = 38.dp) { finish() }
            }
            AndroidView(modifier = Modifier.fillMaxSize(), factory = { ctx ->
                WebView(ctx).apply {
                    web = this
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    // Google не пускает во «встроенные браузеры» — представляемся обычным Chrome
                    settings.userAgentString = settings.userAgentString
                        .replace("; wv", "").replace(Regex("""Version/\d+\.\d+\s"""), "")
                    if (WebViewFeature.isFeatureSupported(WebViewFeature.REQUESTED_WITH_HEADER_ALLOW_LIST)) {
                        WebSettingsCompat.setRequestedWithHeaderOriginAllowList(settings, emptySet())
                    }
                    CookieManager.getInstance().setAcceptCookie(true)
                    CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
                    webViewClient = object : WebViewClient() {
                        override fun onPageFinished(view: WebView?, url: String?) {
                            val c = CookieManager.getInstance().getCookie("https://www.youtube.com")
                            logged = YouTubeCookies.loggedIn(c)
                        }
                    }
                    loadUrl(START_URL)
                }
            })
        }
    }

    companion object {
        const val START_URL = "https://accounts.google.com/ServiceLogin?service=youtube&hl=ru" +
            "&continue=https%3A%2F%2Fwww.youtube.com%2Fsignin%3Faction_handle_signin%3Dtrue%26hl%3Dru" +
            "%26next%3Dhttps%253A%252F%252Fm.youtube.com%252F"
    }
}
