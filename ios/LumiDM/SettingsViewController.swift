import UIKit

class SettingsViewController: UIViewController {

    private let tableView = UITableView(frame: .zero, style: .insetGrouped)

    private enum Row { case folder, maxConc, version }
    private let rows: [Row] = [.folder, .maxConc, .version]

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Settings"
        view.backgroundColor = UIColor(red: 0.067, green: 0.075, blue: 0.090, alpha: 1)

        tableView.backgroundColor = .clear
        tableView.dataSource = self
        tableView.delegate   = self
        tableView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(tableView)
        NSLayoutConstraint.activate([
            tableView.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            tableView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            tableView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            tableView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])
    }
}

// MARK: - UITableViewDataSource + Delegate

extension SettingsViewController: UITableViewDataSource, UITableViewDelegate {

    func tableView(_ tv: UITableView, numberOfRowsInSection section: Int) -> Int { rows.count }

    func tableView(_ tv: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = UITableViewCell(style: .subtitle, reuseIdentifier: nil)
        cell.backgroundColor    = UIColor(red: 0.102, green: 0.114, blue: 0.133, alpha: 1)
        cell.textLabel?.textColor    = UIColor(red: 0.910, green: 0.918, blue: 0.929, alpha: 1)
        cell.detailTextLabel?.textColor = UIColor(red: 0.540, green: 0.560, blue: 0.604, alpha: 1)
        cell.detailTextLabel?.font  = .monospacedSystemFont(ofSize: 11, weight: .regular)

        switch rows[indexPath.row] {
        case .folder:
            cell.textLabel?.text       = "Download Folder"
            cell.detailTextLabel?.text = DownloadEngine.shared.defaultDir
            cell.accessoryType = .disclosureIndicator
        case .maxConc:
            cell.textLabel?.text       = "Simultaneous Downloads"
            cell.detailTextLabel?.text = "\(DownloadEngine.shared.maxConcurrent)"
            cell.accessoryType = .disclosureIndicator
        case .version:
            cell.textLabel?.text       = "Lumi DM"
            cell.detailTextLabel?.text = "v1.0.0"
            cell.selectionStyle = .none
        }
        return cell
    }

    func tableView(_ tv: UITableView, didSelectRowAt indexPath: IndexPath) {
        tv.deselectRow(at: indexPath, animated: true)
        switch rows[indexPath.row] {
        case .folder:
            showFolderPicker()
        case .maxConc:
            showMaxConcPicker()
        case .version:
            break
        }
    }

    private func showFolderPicker() {
        // On iOS we can only write to the app's Documents directory tree
        // Show a document picker to let the user pick a folder
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let alert = UIAlertController(
            title: "Download Folder",
            message: "Downloads save to:\n\(DownloadEngine.shared.defaultDir)\n\nOn iOS, only folders inside your app's Documents directory are writable.",
            preferredStyle: .alert)
        alert.addTextField { tf in
            tf.text                 = DownloadEngine.shared.defaultDir
            tf.textColor            = .label
            tf.autocorrectionType   = .no
            tf.autocapitalizationType = .none
        }
        alert.addAction(UIAlertAction(title: "Save", style: .default) { [weak self] _ in
            let path = alert.textFields?.first?.text?.trimmingCharacters(in: .whitespaces) ?? ""
            if !path.isEmpty {
                try? FileManager.default.createDirectory(
                    atPath: path, withIntermediateDirectories: true)
                DownloadEngine.shared.defaultDir = path
                self?.tableView.reloadData()
            }
        })
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
        present(alert, animated: true)
    }

    private func showMaxConcPicker() {
        let alert = UIAlertController(
            title: "Simultaneous Downloads",
            message: "Choose how many files download at once (1–8)",
            preferredStyle: .actionSheet)
        for n in 1...8 {
            let isCurrent = n == DownloadEngine.shared.maxConcurrent
            alert.addAction(UIAlertAction(
                title: isCurrent ? "✓ \(n)" : "\(n)",
                style: .default) { [weak self] _ in
                DownloadEngine.shared.maxConcurrent = n
                self?.tableView.reloadData()
            })
        }
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
        // iPad support
        if let pop = alert.popoverPresentationController {
            pop.sourceView = tableView
        }
        present(alert, animated: true)
    }
}
