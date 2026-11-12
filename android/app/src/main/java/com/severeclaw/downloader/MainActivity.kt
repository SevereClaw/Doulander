package com.severeclaw.downloader

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.mutableStateOf
import androidx.core.content.ContextCompat
import kotlinx.coroutines.launch
import java.io.File

class MainActivity : ComponentActivity() {
    private val tab = mutableStateOf("main")
    private val askPerms = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestPermissions()
        if (savedInstanceState == null) handleIntent(intent)
        // автопроверка на сервере сборки: am start ... --es selftest_url http://...
        intent.getStringExtra("selftest_url")?.let { url ->
            val out = File(getExternalFilesDir(null), "selftest.log")
            Engine.scope.launch {
                Engine.selftest(url, out, intent.getStringExtra("sync_host"), intent.getStringExtra("sync_code"),
                    intent.getStringExtra("slow_url"))
            }
        }
        // незаконченные загрузки прошлого запуска
        if (Engine.items.value.any { it.state == State.WAIT }) Engine.startService()
        setContent { DownloaderApp(tab.value) { tab.value = it } }
        Updater.startAutoCheck()
        // новая версия скачалась, пока приложение открыто, — сразу предлагаем поставить
        updWatch = Engine.scope.launch {
            Updater.state.collect { st ->
                if (st is Updater.St.Ready && resumed) runOnUiThread { Updater.autoInstall(this@MainActivity) }
            }
        }
    }

    @Volatile private var resumed = false

    companion object {
        /** Приложение сейчас на экране. */
        @Volatile var visible = false
    }
    private var updWatch: kotlinx.coroutines.Job? = null

    override fun onDestroy() {
        updWatch?.cancel()
        super.onDestroy()
    }

    override fun onResume() {
        super.onResume()
        resumed = true
        visible = true
        Updater.autoInstall(this)
    }

    override fun onPause() {
        resumed = false
        visible = false
        super.onPause()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    /** «Поделиться» → Downloader из YouTube, VK, браузера… — ссылка сразу в очередь. */
    private fun handleIntent(i: Intent?) {
        i ?: return
        when (val t = i.getStringExtra("tab")) {
            null -> {}
            "update" -> { tab.value = "settings"; Updater.install(this) }
            else -> tab.value = t
        }
        if (i.getBooleanExtra("login", false)) openLogin(this)
        // для снимка экрана на сервере сборки: редактор с последним скачанным видео
        if (i.getBooleanExtra("edit_latest", false)) {
            Engine.history.value.firstOrNull { it.file.endsWith(".mp4") && "(обрезка" !in it.file }
                ?.let { openEditor(this, it.file) }
        }
        if (i.action == Intent.ACTION_SEND) {
            val text = i.getStringExtra(Intent.EXTRA_TEXT) ?: return
            tab.value = "main"
            val n = Engine.add(text)
            if (n > 0) Engine.message.value = "Добавлено: ${Engine.settings.value.opts.describe()} " +
                    "— удерживайте карточку, чтобы выбрать другое"
        }
    }

    private fun requestPermissions() {
        val need = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= 33) need += Manifest.permission.POST_NOTIFICATIONS
        if (Build.VERSION.SDK_INT <= 28) need += Manifest.permission.WRITE_EXTERNAL_STORAGE
        val missing = need.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) askPerms.launch(missing.toTypedArray())
    }
}
