package com.lumi.dm.engine;

import org.schabi.newpipe.extractor.NewPipe;
import org.schabi.newpipe.extractor.ServiceList;
import org.schabi.newpipe.extractor.services.youtube.extractors.YoutubeStreamExtractor;
import org.schabi.newpipe.extractor.stream.StreamExtractor;
import org.schabi.newpipe.extractor.stream.StreamInfo;
import org.schabi.newpipe.extractor.stream.VideoStream;
import org.schabi.newpipe.extractor.stream.AudioStream;
import org.schabi.newpipe.extractor.downloader.Downloader;
import org.schabi.newpipe.extractor.downloader.Request;
import org.schabi.newpipe.extractor.downloader.Response;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class VideoExtractor {

    public static class VideoFormat {
        public String label;
        public String url;
        public String quality;   // e.g. "1080p", "720p"
        public boolean hasAudio;

        public VideoFormat(String label, String url, String quality, boolean hasAudio) {
            this.label    = label;
            this.url      = url;
            this.quality  = quality;
            this.hasAudio = hasAudio;
        }
    }

    public interface Callback {
        void onFormats(List<VideoFormat> formats, String title);
        void onError(String message);
    }

    private static boolean _initialized = false;

    private static synchronized void ensureInit() {
        if (_initialized) return;
        try {
            NewPipe.init(new SimpleDownloader());
            _initialized = true;
        } catch (Exception e) {
            // ignore — will fail gracefully on extract
        }
    }

    public static void extractFormats(String pageUrl, Callback callback) {
        new Thread(() -> {
            try {
                ensureInit();
                StreamInfo info = StreamInfo.getInfo(pageUrl);
                String title    = info.getName();
                List<VideoFormat> formats = new ArrayList<>();

                // Combined streams (video + audio, best quality first)
                for (VideoStream vs : info.getVideoStreams()) {
                    if (!vs.isVideoOnly()) {
                        String url = vs.isUrl() ? vs.getContent() : null;
                        if (url == null || url.isEmpty()) continue;
                        formats.add(new VideoFormat(vs.getResolution(), url, vs.getResolution(), true));
                    }
                }

                // Video-only streams (higher quality, no audio)
                for (VideoStream vs : info.getVideoOnlyStreams()) {
                    String url = vs.isUrl() ? vs.getContent() : null;
                    if (url == null || url.isEmpty()) continue;
                    formats.add(new VideoFormat(vs.getResolution() + " (video only)", url, vs.getResolution(), false));
                }

                // Best audio-only option
                List<AudioStream> audio = info.getAudioStreams();
                if (!audio.isEmpty()) {
                    AudioStream best = audio.get(0);
                    for (AudioStream a : audio) {
                        if (a.getAverageBitrate() > best.getAverageBitrate()) best = a;
                    }
                    String audioUrl = best.isUrl() ? best.getContent() : null;
                    if (audioUrl != null && !audioUrl.isEmpty()) {
                        formats.add(new VideoFormat(
                            "Audio only (" + best.getAverageBitrate() + " kbps)",
                            audioUrl, "audio", true));
                    }
                }

                callback.onFormats(formats, title);
            } catch (Exception e) {
                callback.onError(e.getMessage() != null ? e.getMessage() : "Could not extract video");
            }
        }).start();
    }

    // Minimal downloader implementation for NewPipe
    private static class SimpleDownloader extends Downloader {
        private static final String UA = "Lumi-DM/1.0";

        @Override
        public Response execute(Request request) throws java.io.IOException {
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(request.url()).openConnection();
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(20000);
                conn.setRequestProperty("User-Agent", UA);
                for (Map.Entry<String, List<String>> h : request.headers().entrySet()) {
                    for (String v : h.getValue()) conn.setRequestProperty(h.getKey(), v);
                }
                conn.connect();

                int code = conn.getResponseCode();
                Map<String, List<String>> respHeaders = conn.getHeaderFields();

                StringBuilder sb = new StringBuilder();
                java.io.InputStream stream = code < 400 ? conn.getInputStream() : conn.getErrorStream();
                if (stream != null) {
                    try (BufferedReader r = new BufferedReader(new InputStreamReader(stream, "UTF-8"))) {
                        String line;
                        while ((line = r.readLine()) != null) sb.append(line).append('\n');
                    }
                }
                conn.disconnect();

                Map<String, List<String>> cleaned = new HashMap<>();
                for (Map.Entry<String, List<String>> e : respHeaders.entrySet()) {
                    if (e.getKey() != null) cleaned.put(e.getKey(), e.getValue());
                }
                return new Response(code, conn.getResponseMessage(), cleaned, sb.toString(), request.url());
            } catch (java.io.IOException e) {
                throw e;
            } catch (Exception e) {
                throw new java.io.IOException("Request failed: " + e.getMessage(), e);
            }
        }
    }
}
