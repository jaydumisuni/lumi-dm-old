import UIKit

class DownloadCell: UITableViewCell {

    static let reuseId = "DownloadCell"

    // MARK: - Subviews

    private let lblName    = UILabel()
    private let lblStatus  = UILabel()
    private let progress   = UIProgressView(progressViewStyle: .default)
    private let btnPause   = UIButton(type: .system)
    private let btnResume  = UIButton(type: .system)
    private let btnCancel  = UIButton(type: .system)
    private let btnDelete  = UIButton(type: .system)
    private let btnStack   = UIStackView()

    private var jobId: String?

    // MARK: - Init

    override init(style: UITableViewCell.CellStyle, reuseIdentifier: String?) {
        super.init(style: style, reuseIdentifier: reuseIdentifier)
        setupViews()
    }

    required init?(coder: NSCoder) { fatalError() }

    private func setupViews() {
        backgroundColor = UIColor(red: 0.102, green: 0.114, blue: 0.133, alpha: 1)
        selectionStyle  = .none

        lblName.font          = .systemFont(ofSize: 14, weight: .semibold)
        lblName.textColor     = UIColor(red: 0.910, green: 0.918, blue: 0.929, alpha: 1)
        lblName.lineBreakMode = .byMiddleTruncation

        lblStatus.font      = .monospacedSystemFont(ofSize: 11, weight: .regular)
        lblStatus.textColor = UIColor(red: 0.540, green: 0.560, blue: 0.604, alpha: 1)

        progress.progressTintColor = UIColor(red: 0.310, green: 0.620, blue: 0.973, alpha: 1)
        progress.trackTintColor    = UIColor(red: 0.165, green: 0.180, blue: 0.200, alpha: 1)

        configureButton(btnPause,  title: "⏸",  color: .systemGray)
        configureButton(btnResume, title: "▶",  color: UIColor(red: 0.310, green: 0.620, blue: 0.973, alpha: 1))
        configureButton(btnCancel, title: "✕",  color: .systemGray)
        configureButton(btnDelete, title: "🗑",  color: .systemRed)

        btnPause.addTarget(self,  action: #selector(didTapPause),  for: .touchUpInside)
        btnResume.addTarget(self, action: #selector(didTapResume), for: .touchUpInside)
        btnCancel.addTarget(self, action: #selector(didTapCancel), for: .touchUpInside)
        btnDelete.addTarget(self, action: #selector(didTapDelete), for: .touchUpInside)

        btnStack.axis    = .horizontal
        btnStack.spacing = 4
        [btnPause, btnResume, btnCancel, btnDelete].forEach { btnStack.addArrangedSubview($0) }

        let statusRow = UIStackView(arrangedSubviews: [lblStatus, btnStack])
        statusRow.axis      = .horizontal
        statusRow.alignment = .center
        statusRow.spacing   = 8

        let vStack = UIStackView(arrangedSubviews: [lblName, progress, statusRow])
        vStack.axis         = .vertical
        vStack.spacing      = 6
        vStack.translatesAutoresizingMaskIntoConstraints = false

        contentView.addSubview(vStack)
        NSLayoutConstraint.activate([
            vStack.topAnchor.constraint(equalTo: contentView.topAnchor, constant: 10),
            vStack.bottomAnchor.constraint(equalTo: contentView.bottomAnchor, constant: -10),
            vStack.leadingAnchor.constraint(equalTo: contentView.leadingAnchor, constant: 16),
            vStack.trailingAnchor.constraint(equalTo: contentView.trailingAnchor, constant: -8),
        ])
    }

    private func configureButton(_ btn: UIButton, title: String, color: UIColor) {
        btn.setTitle(title, for: .normal)
        btn.tintColor = color
        btn.titleLabel?.font = .systemFont(ofSize: 16)
        btn.widthAnchor.constraint(equalToConstant: 32).isActive = true
    }

    // MARK: - Configure

    func configure(with job: DownloadJob) {
        jobId = job.id

        lblName.text = job.filename
        progress.setProgress(Float(job.progressPercent) / 100, animated: false)

        switch job.status {
        case .running:
            var s = "\(job.progressPercent)%  ·  \(DownloadJob.fmtSpeed(job.speedBps))"
            let eta = job.etaString()
            if !eta.isEmpty { s += "  ·  \(eta)" }
            lblStatus.text = s
        case .paused:
            var s = "Paused  ·  \(job.progressPercent)%"
            if job.totalBytes > 0 {
                s += "  (\(DownloadJob.fmtSize(job.downloadedBytes)) / \(DownloadJob.fmtSize(job.totalBytes)))"
            }
            lblStatus.text = s
        case .completed:
            lblStatus.text = "✓ Done  ·  \(DownloadJob.fmtSize(job.totalBytes))"
        case .failed:
            lblStatus.text = "✗ \(job.error ?? "Failed")"
            lblStatus.textColor = .systemRed
        case .cancelled:
            lblStatus.text = "Cancelled"
        case .queued:
            lblStatus.text = "Queued"
        }

        let isRunning  = job.status == .running
        let isQueued   = job.status == .queued
        let isPaused   = job.status == .paused
        let isTerminal = job.isTerminal

        btnPause.isHidden  = !(isRunning || isQueued)
        btnResume.isHidden = !isPaused
        btnCancel.isHidden = isTerminal
        btnDelete.isHidden = !isTerminal
    }

    // MARK: - Actions

    @objc private func didTapPause()  { guard let id = jobId else { return }; DownloadEngine.shared.pause(id) }
    @objc private func didTapResume() { guard let id = jobId else { return }; DownloadEngine.shared.resume(id) }
    @objc private func didTapCancel() { guard let id = jobId else { return }; DownloadEngine.shared.cancel(id) }
    @objc private func didTapDelete() { guard let id = jobId else { return }; DownloadEngine.shared.delete(id) }
}
