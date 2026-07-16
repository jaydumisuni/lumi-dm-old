import UIKit

class DownloadsViewController: UIViewController {

    private let tableView   = UITableView()
    private let lblEmpty    = UILabel()
    private let lblSpeed    = UILabel()

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Downloads"
        view.backgroundColor = UIColor(red: 0.067, green: 0.075, blue: 0.090, alpha: 1)

        setupSpeedBar()
        setupTableView()
        setupEmptyLabel()
        setupNavButtons()
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(engineDidUpdate),
            name: DownloadEngine.didUpdateNotification,
            object: nil)
        refresh()
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        NotificationCenter.default.removeObserver(self)
    }

    // MARK: - Setup

    private func setupSpeedBar() {
        lblSpeed.font          = .monospacedSystemFont(ofSize: 13, weight: .regular)
        lblSpeed.textColor     = UIColor(red: 0.310, green: 0.620, blue: 0.973, alpha: 1)
        lblSpeed.textAlignment = .center
        lblSpeed.backgroundColor = UIColor(red: 0.102, green: 0.114, blue: 0.133, alpha: 1)
        lblSpeed.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(lblSpeed)
        NSLayoutConstraint.activate([
            lblSpeed.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            lblSpeed.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            lblSpeed.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            lblSpeed.heightAnchor.constraint(equalToConstant: 32),
        ])
    }

    private func setupTableView() {
        tableView.backgroundColor = .clear
        tableView.separatorColor  = UIColor(red: 0.165, green: 0.180, blue: 0.200, alpha: 1)
        tableView.dataSource = self
        tableView.register(DownloadCell.self, forCellReuseIdentifier: DownloadCell.reuseId)
        tableView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(tableView)
        NSLayoutConstraint.activate([
            tableView.topAnchor.constraint(equalTo: lblSpeed.bottomAnchor),
            tableView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            tableView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            tableView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])
    }

    private func setupEmptyLabel() {
        lblEmpty.text          = "No downloads yet.\nTap + to add one."
        lblEmpty.numberOfLines = 2
        lblEmpty.textAlignment = .center
        lblEmpty.textColor     = UIColor(red: 0.540, green: 0.560, blue: 0.604, alpha: 1)
        lblEmpty.font          = .systemFont(ofSize: 16)
        lblEmpty.isHidden      = true
        lblEmpty.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(lblEmpty)
        NSLayoutConstraint.activate([
            lblEmpty.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            lblEmpty.centerYAnchor.constraint(equalTo: view.centerYAnchor),
        ])
    }

    private func setupNavButtons() {
        navigationItem.rightBarButtonItems = [
            UIBarButtonItem(title: "Clear done", style: .plain, target: self,
                            action: #selector(clearDone)),
            UIBarButtonItem(title: "⏸ All", style: .plain, target: self,
                            action: #selector(pauseAll)),
        ]
    }

    // MARK: - Refresh

    @objc private func engineDidUpdate() { refresh() }

    private func refresh() {
        tableView.reloadData()
        let jobs  = DownloadEngine.shared.jobs
        lblEmpty.isHidden = !jobs.isEmpty
        let speed = DownloadEngine.shared.totalSpeed
        if speed > 0 {
            lblSpeed.text   = "⬇ \(DownloadJob.fmtSpeed(speed))"
            lblSpeed.isHidden = false
        } else {
            lblSpeed.isHidden = true
        }
    }

    // MARK: - Actions

    @objc private func pauseAll()  { DownloadEngine.shared.pauseAll() }
    @objc private func clearDone() { DownloadEngine.shared.clearCompleted() }
}

// MARK: - UITableViewDataSource

extension DownloadsViewController: UITableViewDataSource {
    func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        DownloadEngine.shared.jobs.count
    }

    func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = tableView.dequeueReusableCell(
            withIdentifier: DownloadCell.reuseId, for: indexPath) as! DownloadCell
        cell.configure(with: DownloadEngine.shared.jobs[indexPath.row])
        return cell
    }
}
