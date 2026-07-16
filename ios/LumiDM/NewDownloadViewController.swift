import UIKit

class NewDownloadViewController: UIViewController {

    // MARK: - UI

    private let scrollView   = UIScrollView()
    private let stack        = UIStackView()
    private let tfUrl        = UITextField()
    private let tfFilename   = UITextField()
    private let segType      = UISegmentedControl(items: ["Direct Download", "Video / YouTube"])
    private let videoSection = UIStackView()
    private let btnAnalyse   = UIButton(type: .system)
    private let spinner      = UIActivityIndicatorView(style: .medium)
    private let pickerQuality = UIPickerView()
    private let lblQualityPlaceholder = UILabel()
    private let lblError     = UILabel()
    private let btnDownload  = UIButton(type: .system)

    private var formats: [VideoFormat] = []

    // Pre-filled URL from share extension or app open
    var prefillURL: String?

    // MARK: - Lifecycle

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Add Download"
        view.backgroundColor = UIColor(red: 0.067, green: 0.075, blue: 0.090, alpha: 1)
        setupScrollView()
        setupFields()
        if let url = prefillURL { tfUrl.text = url }
        NotificationHelper.shared.resetCompletionFlag()
    }

    // MARK: - Layout

    private func setupScrollView() {
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(scrollView)
        NSLayoutConstraint.activate([
            scrollView.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            scrollView.bottomAnchor.constraint(equalTo: view.keyboardLayoutGuide.topAnchor),
            scrollView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            scrollView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])

        stack.axis    = .vertical
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        scrollView.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: scrollView.topAnchor, constant: 20),
            stack.bottomAnchor.constraint(equalTo: scrollView.bottomAnchor, constant: -20),
            stack.leadingAnchor.constraint(equalTo: scrollView.leadingAnchor, constant: 16),
            stack.trailingAnchor.constraint(equalTo: scrollView.trailingAnchor, constant: -16),
            stack.widthAnchor.constraint(equalTo: scrollView.widthAnchor, constant: -32),
        ])
    }

    private func setupFields() {
        // URL
        stack.addArrangedSubview(sectionLabel("URL"))
        styleField(tfUrl, placeholder: "https://…", keyboard: .URL)
        tfUrl.autocorrectionType      = .no
        tfUrl.autocapitalizationType  = .none
        stack.addArrangedSubview(tfUrl)

        // Type picker
        stack.addArrangedSubview(sectionLabel("Type"))
        segType.selectedSegmentIndex = 0
        segType.addTarget(self, action: #selector(typeChanged), for: .valueChanged)
        stack.addArrangedSubview(segType)

        // Video section (hidden by default)
        videoSection.axis    = .vertical
        videoSection.spacing = 10
        videoSection.isHidden = true

        btnAnalyse.setTitle("Analyse Video", for: .normal)
        btnAnalyse.backgroundColor = UIColor(red: 0.102, green: 0.114, blue: 0.133, alpha: 1)
        btnAnalyse.layer.cornerRadius = 8
        btnAnalyse.heightAnchor.constraint(equalToConstant: 44).isActive = true
        btnAnalyse.addTarget(self, action: #selector(analyseVideo), for: .touchUpInside)

        spinner.hidesWhenStopped = true
        spinner.color            = UIColor(red: 0.310, green: 0.620, blue: 0.973, alpha: 1)

        let analyseRow = UIStackView(arrangedSubviews: [btnAnalyse, spinner])
        analyseRow.axis    = .horizontal
        analyseRow.spacing = 12
        analyseRow.alignment = .center
        videoSection.addArrangedSubview(analyseRow)

        lblQualityPlaceholder.text      = "Tap Analyse to load quality options"
        lblQualityPlaceholder.textColor = UIColor(red: 0.540, green: 0.560, blue: 0.604, alpha: 1)
        lblQualityPlaceholder.font      = .systemFont(ofSize: 13)
        videoSection.addArrangedSubview(lblQualityPlaceholder)

        pickerQuality.dataSource = self
        pickerQuality.delegate   = self
        pickerQuality.backgroundColor = UIColor(red: 0.102, green: 0.114, blue: 0.133, alpha: 1)
        pickerQuality.isHidden   = true
        pickerQuality.heightAnchor.constraint(equalToConstant: 120).isActive = true
        videoSection.addArrangedSubview(pickerQuality)

        stack.addArrangedSubview(videoSection)

        // Filename
        stack.addArrangedSubview(sectionLabel("Filename (optional)"))
        styleField(tfFilename, placeholder: "Leave blank to auto-detect", keyboard: .default)
        stack.addArrangedSubview(tfFilename)

        // Error
        lblError.textColor     = .systemRed
        lblError.font          = .systemFont(ofSize: 13)
        lblError.numberOfLines = 0
        lblError.isHidden      = true
        stack.addArrangedSubview(lblError)

        // Download button
        btnDownload.setTitle("Download", for: .normal)
        btnDownload.backgroundColor   = UIColor(red: 0.310, green: 0.620, blue: 0.973, alpha: 1)
        btnDownload.setTitleColor(.white, for: .normal)
        btnDownload.titleLabel?.font  = .systemFont(ofSize: 16, weight: .semibold)
        btnDownload.layer.cornerRadius = 10
        btnDownload.heightAnchor.constraint(equalToConstant: 50).isActive = true
        btnDownload.addTarget(self, action: #selector(startDownload), for: .touchUpInside)
        stack.addArrangedSubview(btnDownload)
    }

    private func sectionLabel(_ text: String) -> UILabel {
        let l = UILabel()
        l.text      = text
        l.font      = .systemFont(ofSize: 12, weight: .regular)
        l.textColor = UIColor(red: 0.540, green: 0.560, blue: 0.604, alpha: 1)
        return l
    }

    private func styleField(_ tf: UITextField, placeholder: String, keyboard: UIKeyboardType) {
        tf.placeholder       = placeholder
        tf.keyboardType      = keyboard
        tf.textColor         = UIColor(red: 0.910, green: 0.918, blue: 0.929, alpha: 1)
        tf.backgroundColor   = UIColor(red: 0.102, green: 0.114, blue: 0.133, alpha: 1)
        tf.layer.cornerRadius = 8
        tf.leftView          = UIView(frame: CGRect(x: 0, y: 0, width: 12, height: 0))
        tf.leftViewMode      = .always
        tf.heightAnchor.constraint(equalToConstant: 44).isActive = true
        let attrs: [NSAttributedString.Key: Any] = [
            .foregroundColor: UIColor(red: 0.540, green: 0.560, blue: 0.604, alpha: 1)
        ]
        tf.attributedPlaceholder = NSAttributedString(string: placeholder, attributes: attrs)
    }

    // MARK: - Actions

    @objc private func typeChanged() {
        videoSection.isHidden = segType.selectedSegmentIndex == 0
    }

    @objc private func analyseVideo() {
        let url = tfUrl.text?.trimmingCharacters(in: .whitespaces) ?? ""
        guard !url.isEmpty else { showError("Enter a URL first"); return }
        lblError.isHidden = true
        spinner.startAnimating()
        btnAnalyse.isEnabled = false
        formats = []

        VideoExtractor.extractFormats(from: url) { [weak self] fmts, title, error in
            DispatchQueue.main.async {
                self?.spinner.stopAnimating()
                self?.btnAnalyse.isEnabled = true
                if let error {
                    self?.showError(error.localizedDescription); return
                }
                self?.formats = fmts
                self?.pickerQuality.reloadAllComponents()
                self?.pickerQuality.isHidden        = false
                self?.lblQualityPlaceholder.isHidden = true
                if let title, !title.isEmpty,
                   self?.tfFilename.text?.isEmpty == true {
                    self?.tfFilename.text = self?.sanitise(title).appending(".mp4")
                }
            }
        }
    }

    @objc private func startDownload() {
        let url = tfUrl.text?.trimmingCharacters(in: .whitespaces) ?? ""
        guard !url.isEmpty else { showError("Enter a URL"); return }

        let filename = tfFilename.text?.trimmingCharacters(in: .whitespaces)
        let isVideo  = segType.selectedSegmentIndex == 1

        if isVideo {
            guard !formats.isEmpty else { showError("Tap Analyse first"); return }
            let idx = pickerQuality.selectedRow(inComponent: 0)
            guard idx < formats.count else { return }
            let fmt = formats[idx]
            DownloadEngine.shared.addVideo(
                url: fmt.url,
                filename: filename?.isEmpty == false ? filename : sanitise(fmt.quality) + ".mp4")
        } else {
            DownloadEngine.shared.addHttp(
                url: url,
                filename: filename?.isEmpty == false ? filename : nil)
        }

        tfUrl.text      = ""
        tfFilename.text = ""
        formats         = []
        pickerQuality.reloadAllComponents()
        pickerQuality.isHidden        = true
        lblQualityPlaceholder.isHidden = false

        // Switch to Downloads tab
        tabBarController?.selectedIndex = 0
    }

    private func showError(_ msg: String) {
        lblError.text     = msg
        lblError.isHidden = false
    }

    private func sanitise(_ s: String) -> String {
        s.components(separatedBy: CharacterSet(charactersIn: "\\/:*?\"<>|"))
         .joined(separator: "_")
         .trimmingCharacters(in: .whitespaces)
    }
}

// MARK: - Picker

extension NewDownloadViewController: UIPickerViewDataSource, UIPickerViewDelegate {
    func numberOfComponents(in pickerView: UIPickerView) -> Int { 1 }
    func pickerView(_ pickerView: UIPickerView, numberOfRowsInComponent component: Int) -> Int {
        formats.count
    }
    func pickerView(_ pickerView: UIPickerView,
                    titleForRow row: Int,
                    forComponent component: Int) -> String? {
        formats[row].label
    }
    func pickerView(_ pickerView: UIPickerView,
                    attributedTitleForRow row: Int,
                    forComponent component: Int) -> NSAttributedString? {
        NSAttributedString(string: formats[row].label,
                           attributes: [.foregroundColor: UIColor(red: 0.910, green: 0.918, blue: 0.929, alpha: 1)])
    }
}
