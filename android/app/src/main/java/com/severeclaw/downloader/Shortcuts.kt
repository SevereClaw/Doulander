package com.severeclaw.downloader

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import android.widget.Toast
import androidx.activity.ComponentActivity

/** Какой вид загрузки взять для «Скачать видео» / «Скачать звук». */
fun optsFor(mode: String?): Opts {
    val cur = Engine.settings.value.opts
    return when (mode) {
        "audio" -> if (cur.isAudio) cur else Opts("audio", "best", "mp3")
        "video" -> if (!cur.isAudio) cur else Opts()
        else -> cur
    }
}

/** Добавить и коротко сообщить, не открывая приложение. */
fun quickAdd(c: Context, text: String?, mode: String?): Boolean {
    if (text.isNullOrBlank() || !Regex("""https?://""").containsMatchIn(text)) {
        Toast.makeText(c, "Downloader: в тексте нет ссылки", Toast.LENGTH_SHORT).show()
        android.util.Log.i("DLSHARE", "nolink")
        return false
    }
    val opts = optsFor(mode)
    val n = Engine.add(text, opts)
    val msg = if (n > 0) "⬇ ${opts.describe()} — в очереди" else "Эта ссылка уже в очереди"
    Toast.makeText(c, "Downloader: $msg", Toast.LENGTH_SHORT).show()
    android.util.Log.i("DLSHARE", "${opts.mode} $n")
    Engine.log("Из «Поделиться»/шторки: ${opts.describe()} ($n)")
    return n > 0
}

/** «Поделиться» → «Скачать видео» / «Скачать звук»: ссылка в очередь, а вы остаётесь в YouTube. */
class ShareActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val alias = intent?.component?.className ?: componentName.className
        val mode = if (alias.endsWith("ShareAudio")) "audio" else "video"
        if (intent?.action == Intent.ACTION_SEND) {
            quickAdd(this, intent.getStringExtra(Intent.EXTRA_TEXT), mode)
        }
        finish()
    }
}

/** Плитка в шторке: прозрачное окно на мгновение — Android даёт читать буфер только окну на экране. */
class ClipActivity : ComponentActivity() {
    private var done = false

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus || done) return
        done = true
        val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
        val text = cm.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString()
        quickAdd(this, text, intent.getStringExtra("mode"))
        finish()
    }
}

abstract class ClipTile(private val mode: String) : TileService() {
    override fun onStartListening() {
        super.onStartListening()
        val tile = qsTile ?: return
        val run = Engine.items.value.count { it.state == State.RUN }
        tile.state = if (run > 0) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        if (Build.VERSION.SDK_INT >= 29) {
            tile.subtitle = if (run > 0) "Качается: $run" else "Ссылка из буфера"
        }
        tile.updateTile()
    }

    override fun onClick() {
        super.onClick()
        val i = Intent(this, ClipActivity::class.java).putExtra("mode", mode)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (Build.VERSION.SDK_INT >= 34) {
            startActivityAndCollapse(PendingIntent.getActivity(this, mode.hashCode(), i,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
        } else {
            @Suppress("DEPRECATION", "StartActivityAndCollapseDeprecated")
            startActivityAndCollapse(i)
        }
    }
}

class VideoTile : ClipTile("video")
class AudioTile : ClipTile("audio")

/** После перезагрузки телефона — продолжить недокачанное. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        if (i.action != Intent.ACTION_BOOT_COMPLETED) return
        if (Engine.items.value.any { it.state == State.WAIT }) {
            Engine.log("Телефон перезагружен — продолжаю загрузки")
            Engine.startService()
        }
    }
}

/** Кнопки в уведомлении: «Пауза» / «Продолжить». */
class NotifActionReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        when (i.action) {
            ACTION_PAUSE_ALL -> Engine.pauseAll()
            ACTION_RESUME_ALL -> Engine.resumeAll()
        }
    }

    companion object {
        const val ACTION_PAUSE_ALL = "com.severeclaw.downloader.PAUSE_ALL"
        const val ACTION_RESUME_ALL = "com.severeclaw.downloader.RESUME_ALL"
    }
}
