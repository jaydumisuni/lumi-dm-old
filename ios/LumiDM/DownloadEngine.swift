import Foundation

final class DownloadEngine {

    static let shared = DownloadEngine()

    // Broadcast when jobs change — observers register on main thread
    static let didUpdateNotification = Notification.Name("DownloadEngineDidUpdate")

    private(set) var jobs: [DownloadJob] = []
    private var tasks:     [String: HttpDownloadTask] = [:]

    var maxConcurrent: Int = 3 { didSet { savePrefs() } }
    var defaultDir: String    { didSet { savePrefs() } }

    private init() {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        defaultDir = docs.appendingPathComponent("Downloads").path
        try? FileManager.default.createDirectory(
            atPath: defaultDir, withIntermediateDirectories: true)
        loadPrefs()
        loadState()
    }

    // MARK: - Add

    @discardableResult
    func addHttp(url: String, filename: String? = nil, dir: String? = nil) -> DownloadJob {
        let name = filename ?? guessFilename(from: url)
        let job  = DownloadJob(url: url, filename: name,
                               targetDir: dir ?? defaultDir, type: .http)
        enqueue(job)
        return job
    }

    @discardableResult
    func addVideo(url: String, filename: String? = nil, dir: String? = nil) -> DownloadJob {
        let name = filename ?? "video_\(Int(Date().timeIntervalSince1970)).mp4"
        let job  = DownloadJob(url: url, filename: name,
                               targetDir: dir ?? defaultDir, type: .video)
        enqueue(job)
        return job
    }

    private func enqueue(_ job: DownloadJob) {
        jobs.insert(job, at: 0)
        saveState()
        startNextIfNeeded()
        broadcast()
    }

    // MARK: - Controls

    func pause(_ id: String) {
        tasks[id]?.pause()
    }

    func resume(_ id: String) {
        guard let job = jobs.first(where: { $0.id == id }),
              job.status == .paused || job.status == .failed else { return }
        job.status = .queued
        startNextIfNeeded()
        broadcast()
    }

    func cancel(_ id: String) {
        tasks[id]?.cancel()
        tasks.removeValue(forKey: id)
        if let job = jobs.first(where: { $0.id == id }), !job.isTerminal {
            job.status = .cancelled
            saveState()
            broadcast()
        }
    }

    func delete(_ id: String) {
        cancel(id)
        jobs.removeAll { $0.id == id }
        saveState()
        broadcast()
    }

    func pauseAll()      { jobs.filter { $0.status == .running  }.forEach { pause($0.id) } }
    func resumeAll()     { jobs.filter { $0.status == .paused   }.forEach { resume($0.id) } }
    func clearCompleted(){ jobs.filter { $0.isTerminal          }.forEach { delete($0.id) } }

    var totalSpeed: Int64 {
        jobs.filter { $0.status == .running }.reduce(0) { $0 + $1.speedBps }
    }

    // MARK: - Internal queue

    private func startNextIfNeeded() {
        let running = jobs.filter { $0.status == .running }.count
        guard running < maxConcurrent else { return }
        guard let next = jobs.first(where: { $0.status == .queued }) else { return }
        submit(next)
    }

    private func submit(_ job: DownloadJob) {
        let task = HttpDownloadTask(job: job) { [weak self] updated in
            self?.saveState()
            self?.broadcast()
            // Clean up task ref when done or paused (paused creates a fresh task on resume)
            if updated.isTerminal || updated.status == .paused {
                self?.tasks.removeValue(forKey: updated.id)
            }
            if updated.isTerminal {
                self?.startNextIfNeeded()
                if updated.status == .completed {
                    NotificationHelper.shared.sendCompletionIfAllDone(
                        jobs: self?.jobs ?? [])
                }
            }
        }
        tasks[job.id] = task
        task.start()
    }

    // MARK: - Broadcast

    private func broadcast() {
        DispatchQueue.main.async {
            NotificationCenter.default.post(
                name: DownloadEngine.didUpdateNotification, object: self)
        }
    }

    // MARK: - Persistence

    private var stateURL: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("downloads.json")
    }

    private var prefsURL: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("LUMIDM_prefs.json")
    }

    func saveState() {
        // Encode newest-first; on load we reverse
        if let data = try? JSONEncoder().encode(jobs) {
            try? data.write(to: stateURL)
        }
    }

    private func loadState() {
        guard let data  = try? Data(contentsOf: stateURL),
              let saved = try? JSONDecoder().decode([DownloadJob].self, from: data)
        else { return }
        // Mark any in-progress jobs as paused (app was killed mid-download)
        saved.forEach { if $0.status == .running { $0.status = .paused } }
        jobs = saved
    }

    func savePrefs() {
        let d: [String: Any] = ["defaultDir": defaultDir, "maxConcurrent": maxConcurrent]
        if let data = try? JSONSerialization.data(withJSONObject: d) {
            try? data.write(to: prefsURL)
        }
    }

    private func loadPrefs() {
        guard let data  = try? Data(contentsOf: prefsURL),
              let prefs = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return }
        if let dir = prefs["defaultDir"]    as? String { defaultDir    = dir }
        if let max = prefs["maxConcurrent"] as? Int    { maxConcurrent = max }
    }

    // MARK: - Helpers

    private func guessFilename(from urlStr: String) -> String {
        let name = URL(string: urlStr)?.lastPathComponent ?? ""
        if !name.isEmpty && name != "/" {
            return name.components(separatedBy: "?")[0]
        }
        return "download_\(Int(Date().timeIntervalSince1970))"
    }
}
