package com.severeclaw.downloader

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ContentResolver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.ServiceCompat
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

/** Держит загрузки живыми, когда приложение свёрнуто, и показывает прогресс в шторке. */
class DownloadService : Service() {
    private var watcher: Job? = null
    private var wake: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val n = progressNotification(this, "Загрузка…", 0, 0, true)
        ServiceCompat.startForeground(this, ID_PROGRESS, n,
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0)
        if (wake == null) {
            wake = (getSystemService(POWER_SERVICE) as PowerManager)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Downloader:dl")
                .apply { setReferenceCounted(false); acquire(6 * 3600 * 1000L) }
        }
        if (watcher == null) {
            watcher = Engine.scope.launch {
                Engine.items.collectLatest { list ->
                    val run = list.filter { it.state == State.RUN }
                    val wait = list.count { it.state == State.WAIT }
                    if (run.isEmpty() && wait == 0) {
                        stopSelf()
                        return@collectLatest
                    }
                    val first = run.firstOrNull()
                    val title = when {
                        run.size > 1 -> "Качается: ${run.size}" + if (wait > 0) ", в очереди: $wait" else ""
                        first != null -> first.label
                        else -> "В очереди: $wait"
                    }
                    val p = if (run.isEmpty()) 0 else (run.map { it.progress }.average() * 100).toInt()
                    val text = first?.status ?: "Ожидание…"
                    runCatching {
                        NotificationManagerCompat.from(this@DownloadService).notify(ID_PROGRESS,
                            progressNotification(this@DownloadService, title, p, 100, run.isEmpty(), text))
                    }
                }
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        watcher?.cancel()
        runCatching { wake?.release() }
        super.onDestroy()
    }

    companion object {
        const val CH_PROGRESS = "progress"
        const val CH_DONE = "done"
        const val CH_DONE_SILENT = "done_silent"
        const val ID_PROGRESS = 1
        const val ID_DONE = 2

        fun createChannels(c: Context) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val nm = c.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CH_PROGRESS, "Ход загрузки",
                NotificationManager.IMPORTANCE_LOW))
            val sound = Uri.parse("${ContentResolver.SCHEME_ANDROID_RESOURCE}://${c.packageName}/${R.raw.done}")
            nm.createNotificationChannel(NotificationChannel(CH_DONE, "Загрузка завершена",
                NotificationManager.IMPORTANCE_DEFAULT).apply {
                // звук уведомления — тот же, что в программе для Windows
                setSound(sound, AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_NOTIFICATION).build())
            })
            nm.createNotificationChannel(NotificationChannel(CH_DONE_SILENT,
                "Загрузка завершена (без звука)", NotificationManager.IMPORTANCE_LOW))
        }

        private fun openApp(c: Context, tab: String? = null): PendingIntent =
            PendingIntent.getActivity(c, if (tab == null) 0 else 1,
                Intent(c, MainActivity::class.java).apply {
                    flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
                    if (tab != null) putExtra("tab", tab)
                },
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

        fun progressNotification(c: Context, title: String, p: Int, max: Int, indeterminate: Boolean,
                                 text: String? = null): Notification =
            NotificationCompat.Builder(c, CH_PROGRESS)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle(title)
                .setContentText(text)
                .setColor(0xFFE60012.toInt())
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setProgress(max, p, indeterminate)
                .setContentIntent(openApp(c))
                .build()

        fun notifyDone(c: Context, n: Int, sound: Boolean) {
            val nt = NotificationCompat.Builder(c, if (sound) CH_DONE else CH_DONE_SILENT)
                .setSmallIcon(R.drawable.ic_stat)
                .setColor(0xFFE60012.toInt())
                .setContentTitle("ГОТОВО!")
                .setContentText(if (n == 1) "Файл скачан — Загрузки/Downloader"
                                else "Скачано файлов: $n — Загрузки/Downloader")
                .setAutoCancel(true)
                .setContentIntent(openApp(c, "gallery"))
                .build()
            runCatching { NotificationManagerCompat.from(c).notify(ID_DONE, nt) }
        }
    }
}
