import Foundation

struct VideoFormat {
    let label: String
    let url: String
    let quality: String
    let hasAudio: Bool
}

class VideoExtractor {

    static func extractFormats(
        from pageUrl: String,
        completion: @escaping (_ formats: [VideoFormat], _ title: String?, _ error: Error?) -> Void
    ) {
        guard let url = URL(string: pageUrl) else {
            completion([], nil, makeError("Invalid URL"))
            return
        }

        var request = URLRequest(url: url)
        // Mobile UA so YouTube serves the mobile page (smaller, faster to parse)
        request.setValue(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            forHTTPHeaderField: "User-Agent")
        request.setValue("en-US,en;q=0.9", forHTTPHeaderField: "Accept-Language")

        URLSession.shared.dataTask(with: request) { data, _, error in
            if let error {
                completion([], nil, error); return
            }
            guard let data, let html = String(data: data, encoding: .utf8) else {
                completion([], nil, makeError("Empty response")); return
            }
            do {
                let (formats, title) = try parse(html: html)
                completion(formats, title, nil)
            } catch {
                completion([], nil, error)
            }
        }.resume()
    }

    // MARK: - Parser

    private static func parse(html: String) throws -> ([VideoFormat], String) {
        guard let startRange = html.range(of: "ytInitialPlayerResponse = ") else {
            throw makeError("Not a supported video page")
        }

        // Extract the JSON object by counting braces
        let jsonStr = try extractJSON(from: html, startingAfter: startRange.upperBound)

        guard let jsonData = jsonStr.data(using: .utf8),
              let root = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any] else {
            throw makeError("Could not parse player response")
        }

        let title = (root["videoDetails"] as? [String: Any])?["title"] as? String ?? ""

        guard let streaming = root["streamingData"] as? [String: Any] else {
            throw makeError("No streaming data — video may be private or age-restricted")
        }

        var formats: [VideoFormat] = []

        // Combined streams (video + audio)
        if let fmts = streaming["formats"] as? [[String: Any]] {
            let sorted = fmts.sorted { heightOf($0) > heightOf($1) }
            for f in sorted {
                guard let url = f["url"] as? String else { continue }
                let h     = heightOf(f)
                let label = h > 0 ? "\(h)p" : "Unknown"
                formats.append(VideoFormat(label: label, url: url, quality: label, hasAudio: true))
            }
        }

        // Audio-only (best bitrate)
        if let adaptive = streaming["adaptiveFormats"] as? [[String: Any]] {
            let audioFmts = adaptive.filter {
                ($0["mimeType"] as? String)?.hasPrefix("audio/") == true
            }
            if let best = audioFmts.max(by: { bitrateOf($0) < bitrateOf($1) }),
               let url = best["url"] as? String {
                let kbps = bitrateOf(best) / 1000
                formats.append(VideoFormat(
                    label: "Audio only (\(kbps) kbps)",
                    url: url,
                    quality: "audio",
                    hasAudio: true))
            }
        }

        if formats.isEmpty {
            throw makeError("No downloadable formats found")
        }
        return (formats, title)
    }

    private static func extractJSON(from html: String, startingAfter start: String.Index) throws -> String {
        var depth    = 0
        var inString = false
        var escaped  = false
        var end      = start

        for idx in html[start...].indices {
            let ch = html[idx]
            if escaped                { escaped = false; end = html.index(after: idx); continue }
            if ch == "\\" && inString { escaped = true;  end = html.index(after: idx); continue }
            if ch == "\""             { inString = !inString }
            if !inString {
                if ch == "{"  { depth += 1 }
                else if ch == "}" {
                    depth -= 1
                    if depth == 0 { end = html.index(after: idx); break }
                }
            }
            end = html.index(after: idx)
        }
        guard depth == 0 else { throw makeError("Malformed JSON in player response") }
        return String(html[start..<end])
    }

    private static func heightOf(_ f: [String: Any]) -> Int { f["height"] as? Int ?? 0 }
    private static func bitrateOf(_ f: [String: Any]) -> Int { f["bitrate"] as? Int ?? 0 }

    private static func makeError(_ msg: String) -> NSError {
        NSError(domain: "VideoExtractor", code: 0, userInfo: [NSLocalizedDescriptionKey: msg])
    }
}
