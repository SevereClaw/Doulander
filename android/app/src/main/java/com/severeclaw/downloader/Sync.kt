package com.severeclaw.downloader

import android.os.Build
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID

/** Запись истории ПК (файл лежит на компьютере). */
data class PcRec(val id: String, val url: String, val title: String, val name: String,
                 val ext: String, val size: Long, val date: Long, val opts: Opts, val duration: Int?)

/** Общая история с программой на ПК по домашнему Wi-Fi (см. sync_server.py). */
object Sync {
    const val HTTP_PORT = 47614
    const val UDP_PORT = 47615

    val pcItems = MutableStateFlow<List<PcRec>>(emptyList())
    val status = MutableStateFlow("")
    val busy = MutableStateFlow(false)
    private var lastOk = 0L

    private val cache: File get() = File(Engine.app.filesDir, "pc_history.json")
    val paired: Boolean get() = Engine.settings.value.syncKey.isNotEmpty()

    fun init() {
        if (Engine.settings.value.deviceId.isEmpty()) {
            Engine.saveSettings(Engine.settings.value.copy(deviceId = UUID.randomUUID().toString()))
        }
        runCatching { pcItems.value = parse(JSONObject(cache.readText()).getJSONArray("items")) }
        if (paired) status.value = "Подключено к ${Engine.settings.value.syncName}"
        // синхронизация при запуске и потом раз в 10 минут, пока приложение работает
        Engine.scope.launch {
            delay(3000)
            while (true) {
                if (paired) sync(quiet = true)
                delay(10 * 60 * 1000L)
            }
        }
    }

    private fun parse(a: JSONArray) = List(a.length()) { a.getJSONObject(it) }.mapNotNull { j ->
        val id = j.optString("id")
        if (id.isEmpty()) null else PcRec(id, j.optString("url"), j.optString("title"),
            j.optString("name"), j.optString("ext"), j.optLong("size"), j.optLong("date"),
            Opts.fromJson(j.optJSONObject("opts")),
            if (j.isNull("duration")) null else j.optInt("duration").takeIf { it > 0 })
    }

    fun base(host: String = Engine.settings.value.syncHost) =
        "http://" + if (':' in host) host else "$host:$HTTP_PORT"

    fun thumbUrl(id: String) = "${base()}/api/thumb/$id?key=${Engine.settings.value.syncKey}"
    fun fileUrl(id: String) = "${base()}/api/file/$id"

    fun conn(url: String, method: String = "GET", body: String? = null, timeout: Int = 6000): HttpURLConnection {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = timeout
        c.readTimeout = 20000
        c.requestMethod = method
        Engine.settings.value.syncKey.takeIf { it.isNotEmpty() }?.let { c.setRequestProperty("X-Key", it) }
        if (body != null) {
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            c.outputStream.use { it.write(body.toByteArray()) }
        }
        return c
    }

    /** Поиск ПК в сети: широковещательный вопрос, ПК с включённой синхронизацией отвечают. */
    suspend fun discover(): List<Pair<String, String>> = withContext(Dispatchers.IO) {
        val found = linkedMapOf<String, String>()
        runCatching {
            DatagramSocket().use { s ->
                s.broadcast = true
                s.soTimeout = 700
                val msg = "DOULANDER?".toByteArray()
                repeat(3) {
                    runCatching {
                        s.send(DatagramPacket(msg, msg.size, InetAddress.getByName("255.255.255.255"), UDP_PORT))
                    }
                    val end = System.currentTimeMillis() + 900
                    while (System.currentTimeMillis() < end) {
                        val buf = ByteArray(512)
                        val p = DatagramPacket(buf, buf.size)
                        try {
                            s.receive(p)
                        } catch (e: SocketTimeoutException) {
                            break
                        }
                        val j = runCatching { JSONObject(String(p.data, 0, p.length)) }.getOrNull() ?: continue
                        if (j.optString("app") != "doulander") continue
                        found["${p.address.hostAddress}:${j.optInt("port", HTTP_PORT)}"] = j.optString("name", "ПК")
                    }
                }
            }
        }.onFailure { Engine.log("Поиск ПК: $it") }
        found.toList()
    }

    /** Подключение по коду с экрана ПК. */
    suspend fun pair(hostIn: String, code: String): String? = withContext(Dispatchers.IO) {
        val host = hostIn.trim().removePrefix("http://").trimEnd('/')
        try {
            val body = JSONObject().put("code", code.filter { it.isDigit() })
                .put("device", Engine.settings.value.deviceId)
                .put("name", "${Build.MANUFACTURER} ${Build.MODEL}").toString()
            val c = URL("${base(host)}/api/pair").openConnection() as HttpURLConnection
            c.connectTimeout = 6000; c.readTimeout = 10000; c.requestMethod = "POST"; c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            c.outputStream.use { it.write(body.toByteArray()) }
            val code2 = c.responseCode
            if (code2 != 200) {
                val err = runCatching { JSONObject(c.errorStream.bufferedReader().readText()).optString("error") }
                    .getOrNull()
                return@withContext err ?: "ПК ответил: $code2"
            }
            val j = JSONObject(c.inputStream.bufferedReader().readText())
            Engine.saveSettings(Engine.settings.value.copy(syncHost = host, syncKey = j.getString("key"),
                syncName = j.optString("name", "ПК")))
            Engine.log("Подключено к ПК ${j.optString("name")} ($host)")
            sync(quiet = false)
            null
        } catch (e: Throwable) {
            "Не удалось связаться с ПК: ${e.message}. Проверьте, что телефон и ПК в одной сети Wi-Fi"
        }
    }

    fun unpair() {
        Engine.saveSettings(Engine.settings.value.copy(syncHost = "", syncKey = "", syncName = ""))
        pcItems.value = emptyList()
        cache.delete()
        status.value = ""
    }

    /** Своя история — на ПК, история ПК — сюда. */
    fun localRecords(): JSONArray {
        val a = JSONArray()
        val seen = HashSet<String>()
        for (r in Engine.history.value) {
            val f = File(r.file)
            if (!f.isFile || !seen.add(r.file)) continue
            a.put(JSONObject().put("id", UUID.nameUUIDFromBytes(r.file.toByteArray()).toString().take(16))
                .put("url", r.url).put("title", r.title).put("name", f.name)
                .put("ext", f.extension.lowercase()).put("size", f.length()).put("date", r.date)
                .put("opts", r.opts.toJson()).put("duration", r.duration).put("thumb", r.thumb))
        }
        return a
    }

    suspend fun sync(quiet: Boolean): Boolean = withContext(Dispatchers.IO) {
        if (!paired || busy.value) return@withContext false
        busy.value = true
        val s = Engine.settings.value
        try {
            val c = conn("${base()}/api/history")
            if (c.responseCode == 401) {
                status.value = "ПК больше не принимает этот телефон — подключитесь заново"
                return@withContext false
            }
            val j = JSONObject(c.inputStream.bufferedReader().readText())
            pcItems.value = parse(j.getJSONArray("items"))
            cache.writeText(j.toString())
            val body = JSONObject().put("device", s.deviceId).put("name", "${Build.MANUFACTURER} ${Build.MODEL}")
                .put("items", localRecords()).toString()
            conn("${base()}/api/history", "POST", body).responseCode
            lastOk = System.currentTimeMillis()
            status.value = "Подключено к ${s.syncName} • " +
                SimpleDateFormat("HH:mm", Locale.ROOT).format(Date(lastOk))
            if (!quiet) Engine.message.value = "Синхронизировано с ПК: ${pcItems.value.size} файлов"
            true
        } catch (e: Throwable) {
            status.value = "ПК не в сети" + if (lastOk > 0)
                " (синхронизировано в ${SimpleDateFormat("HH:mm", Locale.ROOT).format(Date(lastOk))})" else ""
            if (!quiet) Engine.message.value = "ПК не отвечает — он включён и в той же сети Wi-Fi?"
            false
        } finally {
            busy.value = false
        }
    }
}
