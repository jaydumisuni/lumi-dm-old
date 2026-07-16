import Foundation

class DownloadJob: Codable {

    enum Status: String, Codable {
        case queued, running, paused, completed, failed, cancelled
    }
    enum JobType: String, Codable {
        case http, video
    }

    let id: String
    var url: String
    var filename: String
    var targetDir: String
    var type: JobType
    var status: Status
    var totalBytes: Int64
    var downloadedBytes: Int64
    var speedBps: Int64
    var error: String?
    let createdAt: Double
    var resumeData: Data?

    init(url: String, filename: String, targetDir: String, type: JobType) {
        self.id             = UUID().uuidString
        self.url            = url
        self.filename       = filename
        self.targetDir      = targetDir
        self.type           = type
        self.status         = .queued
        self.totalBytes     = 0
        self.downloadedBytes = 0
        self.speedBps       = 0
        self.createdAt      = Date().timeIntervalSince1970
    }

    var progressPercent: Int {
        guard totalBytes > 0 else { return 0 }
        return Int(min(100, downloadedBytes * 100 / totalBytes))
    }

    var isTerminal: Bool {
        return status == .completed || status == .failed || status == .cancelled
    }

    var isActive: Bool {
        return status == .running || status == .queued
    }

    func etaString() -> String {
        guard speedBps > 0, totalBytes > downloadedBytes else { return "" }
        let secs = (totalBytes - downloadedBytes) / speedBps
        if secs < 60   { return "\(secs)s left" }
        if secs < 3600 { return "\(secs/60)m \(secs%60)s left" }
        return "\(secs/3600)h \((secs%3600)/60)m left"
    }

    static func fmtSpeed(_ bps: Int64) -> String {
        if bps >= 1_048_576 { return String(format: "%.1f MB/s", Double(bps) / 1_048_576) }
        if bps >= 1_024     { return "\(bps / 1_024) KB/s" }
        return "\(bps) B/s"
    }

    static func fmtSize(_ bytes: Int64) -> String {
        if bytes >= 1_073_741_824 { return String(format: "%.2f GB", Double(bytes) / 1_073_741_824) }
        if bytes >= 1_048_576     { return String(format: "%.1f MB", Double(bytes) / 1_048_576) }
        if bytes >= 1_024         { return "\(bytes / 1_024) KB" }
        return "\(bytes) B"
    }
}
