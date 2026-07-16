import Foundation

class HttpDownloadTask: NSObject {

    let job: DownloadJob
    private let onUpdate: (DownloadJob) -> Void

    private var session: URLSession?
    private var downloadTask: URLSessionDownloadTask?
    private var speedWindowStart = Date()
    private var speedWindowBytes: Int64 = 0

    init(job: DownloadJob, onUpdate: @escaping (DownloadJob) -> Void) {
        self.job      = job
        self.onUpdate = onUpdate
    }

    func start() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest  = 15
        config.timeoutIntervalForResource = 86400   // 24 h for large files
        session = URLSession(configuration: config, delegate: self, delegateQueue: nil)

        if let resumeData = job.resumeData {
            downloadTask = session?.downloadTask(withResumeData: resumeData)
        } else {
            guard let url = URL(string: job.url) else {
                fail("Invalid URL"); return
            }
            var request = URLRequest(url: url)
            request.setValue("Lumi-DM/1.0", forHTTPHeaderField: "User-Agent")
            downloadTask = session?.downloadTask(with: request)
        }

        job.status     = .running
        job.resumeData = nil
        onUpdate(job)
        downloadTask?.resume()
    }

    func pause() {
        downloadTask?.cancel(byProducingResumeData: { [weak self] data in
            guard let self else { return }
            self.job.resumeData = data
            self.job.status     = .paused
            self.job.speedBps   = 0
            self.onUpdate(self.job)
        })
        downloadTask = nil
    }

    func cancel() {
        downloadTask?.cancel()
        downloadTask = nil
        job.status   = .cancelled
        job.speedBps = 0
        onUpdate(job)
    }

    private func fail(_ message: String) {
        job.status   = .failed
        job.error    = message
        job.speedBps = 0
        onUpdate(job)
    }

    private var finalFileURL: URL {
        URL(fileURLWithPath: job.targetDir).appendingPathComponent(job.filename)
    }
}

// MARK: - URLSessionDownloadDelegate

extension HttpDownloadTask: URLSessionDownloadDelegate {

    func urlSession(_ session: URLSession,
                    downloadTask: URLSessionDownloadTask,
                    didFinishDownloadingTo location: URL) {
        do {
            let dest = finalFileURL
            if FileManager.default.fileExists(atPath: dest.path) {
                try FileManager.default.removeItem(at: dest)
            }
            try FileManager.default.moveItem(at: location, to: dest)

            let size = (try? dest.resourceValues(forKeys: [.fileSizeKey]))?.fileSize.map { Int64($0) } ?? 0
            job.downloadedBytes = job.totalBytes > 0 ? job.totalBytes : size
            job.totalBytes      = job.downloadedBytes
            job.speedBps        = 0
            job.status          = .completed
            onUpdate(job)
        } catch {
            fail(error.localizedDescription)
        }
    }

    func urlSession(_ session: URLSession,
                    downloadTask: URLSessionDownloadTask,
                    didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64,
                    totalBytesExpectedToWrite: Int64) {
        job.downloadedBytes = totalBytesWritten
        if totalBytesExpectedToWrite > 0 {
            job.totalBytes = totalBytesExpectedToWrite
        }

        speedWindowBytes += bytesWritten
        let elapsed = Date().timeIntervalSince(speedWindowStart)
        if elapsed >= 0.5 {
            job.speedBps     = Int64(Double(speedWindowBytes) / elapsed)
            speedWindowBytes = 0
            speedWindowStart = Date()
            onUpdate(job)
        }
    }

    func urlSession(_ session: URLSession,
                    task: URLSessionTask,
                    didCompleteWithError error: Error?) {
        guard let error = error as NSError? else { return }
        if error.code == NSURLErrorCancelled { return }   // intentional cancel/pause
        fail(error.localizedDescription)
    }
}
