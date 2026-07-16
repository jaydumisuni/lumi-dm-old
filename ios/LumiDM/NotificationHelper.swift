import Foundation
import UserNotifications

final class NotificationHelper {

    static let shared = NotificationHelper()
    private var completionFired = false

    private init() {}

    func requestPermission() {
        UNUserNotificationCenter.current().requestAuthorization(
            options: [.alert, .sound, .badge]) { _, _ in }
    }

    // Called after every completed download — fires once when ALL are done
    func sendCompletionIfAllDone(jobs: [DownloadJob]) {
        let active = jobs.filter { $0.isActive }.count
        guard active == 0, !jobs.isEmpty else { return }

        let done = jobs.filter { $0.status == .completed }.count
        guard done > 0, !completionFired else { return }
        completionFired = true

        let content        = UNMutableNotificationContent()
        content.title      = "✓ Downloads complete"
        content.body       = "\(done) file\(done == 1 ? "" : "s") finished"
        content.sound      = .default

        let req = UNNotificationRequest(
            identifier: "LUMIDM.alldone",
            content: content,
            trigger: nil)   // deliver immediately

        UNUserNotificationCenter.current().add(req)
    }

    // Reset flag when new downloads are added
    func resetCompletionFlag() {
        completionFired = false
    }

    // Live progress notification for a single download (shown while in background)
    func sendProgressNotification(for job: DownloadJob) {
        let content   = UNMutableNotificationContent()
        content.title = "⬇ \(job.filename)"
        var body      = "\(job.progressPercent)%"
        if job.speedBps > 0 { body += "  ·  \(DownloadJob.fmtSpeed(job.speedBps))" }
        let eta = job.etaString()
        if !eta.isEmpty      { body += "  ·  \(eta)" }
        content.body  = body

        let req = UNNotificationRequest(
            identifier: "LUMIDM.progress.\(job.id)",
            content: content,
            trigger: nil)
        UNUserNotificationCenter.current().add(req)
    }
}
