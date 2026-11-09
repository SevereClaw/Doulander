package com.severeclaw.downloader

import android.app.Application
import coil.ImageLoader
import coil.ImageLoaderFactory
import coil.decode.VideoFrameDecoder

class App : Application(), ImageLoaderFactory {
    override fun onCreate() {
        super.onCreate()
        DownloadService.createChannels(this)
        Engine.init(this)
    }

    /** Картинки: обложки с сайтов и кадры прямо из скачанных видео (для галереи). */
    override fun newImageLoader(): ImageLoader = ImageLoader.Builder(this)
        .components { add(VideoFrameDecoder.Factory()) }
        .crossfade(true)
        .build()
}
