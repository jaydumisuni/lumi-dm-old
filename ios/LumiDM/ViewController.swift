import UIKit
import WebKit

class ViewController: UIViewController, WKNavigationDelegate, WKUIDelegate {

    private var webView: WKWebView!
    private var activityIndicator: UIActivityIndicatorView!

    private var serverURL: String {
        get { UserDefaults.standard.string(forKey: "server_url") ?? "http://192.168.1.100:7000" }
        set { UserDefaults.standard.set(newValue, forKey: "server_url") }
    }

    // MARK: - Lifecycle

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(red: 0.067, green: 0.075, blue: 0.090, alpha: 1)
        title = "Lumi DM"
        setupNavigationBar()
        setupWebView()
        setupActivityIndicator()
        loadServer()
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { [weak self] in
            self?.maybeShowExtensionPrompt()
        }
    }

    private func maybeShowExtensionPrompt() {
        guard !UserDefaults.standard.bool(forKey: "ext_prompt_shown") else { return }
        let alert = UIAlertController(
            title: "Get the Browser Extension",
            message: """
            The Lumi DM extension lets you send any link, torrent, or video \
            URL directly to this download manager from your browser.

            Safari on iOS does not support WebExtensions, but on your desktop:

            Chrome / Edge / Brave:
            1. Go to chrome://extensions
            2. Enable Developer mode
            3. Load unpacked → select browser-extension/

            Firefox:
            1. Go to about:debugging
            2. Load Temporary Add-on → manifest.json
            """,
            preferredStyle: .alert
        )
        alert.addAction(UIAlertAction(title: "Got it", style: .default) { _ in
            UserDefaults.standard.set(true, forKey: "ext_prompt_shown")
        })
        alert.addAction(UIAlertAction(title: "Don't show again", style: .cancel) { _ in
            UserDefaults.standard.set(true, forKey: "ext_prompt_shown")
        })
        present(alert, animated: true)
    }

    // MARK: - Setup

    private func setupNavigationBar() {
        navigationItem.rightBarButtonItems = [
            UIBarButtonItem(image: UIImage(systemName: "gearshape"),
                            style: .plain, target: self,
                            action: #selector(openSettings)),
            UIBarButtonItem(image: UIImage(systemName: "arrow.clockwise"),
                            style: .plain, target: self,
                            action: #selector(reloadPage)),
        ]
    }

    private func setupWebView() {
        let config = WKWebViewConfiguration()
        config.preferences.javaScriptEnabled = true
        config.allowsInlineMediaPlayback = true
        if #available(iOS 14.0, *) {
            config.defaultWebpagePreferences.allowsContentJavaScript = true
        }

        webView = WKWebView(frame: .zero, configuration: config)
        webView.translatesAutoresizingMaskIntoConstraints = false
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.scrollView.bounces = false
        view.addSubview(webView)

        NSLayoutConstraint.activate([
            webView.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            webView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            webView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])
    }

    private func setupActivityIndicator() {
        activityIndicator = UIActivityIndicatorView(style: .large)
        activityIndicator.color = UIColor(red: 0.310, green: 0.620, blue: 0.973, alpha: 1)
        activityIndicator.translatesAutoresizingMaskIntoConstraints = false
        activityIndicator.hidesWhenStopped = true
        view.addSubview(activityIndicator)
        NSLayoutConstraint.activate([
            activityIndicator.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            activityIndicator.centerYAnchor.constraint(equalTo: view.centerYAnchor),
        ])
    }

    // MARK: - Actions

    private func loadServer() {
        guard let url = URL(string: serverURL) else { return }
        activityIndicator.startAnimating()
        webView.load(URLRequest(url: url))
    }

    @objc private func reloadPage() {
        if webView.url != nil {
            webView.reload()
        } else {
            loadServer()
        }
    }

    @objc private func openSettings() {
        let alert = UIAlertController(
            title: "Server URL",
            message: "Enter the Lumi DM server address.\nExample: http://192.168.1.100:7000",
            preferredStyle: .alert
        )
        alert.addTextField { tf in
            tf.text = self.serverURL
            tf.placeholder = "http://192.168.1.100:7000"
            tf.keyboardType = .URL
            tf.autocorrectionType = .no
            tf.autocapitalizationType = .none
        }
        alert.addAction(UIAlertAction(title: "Save & Connect", style: .default) { [weak self] _ in
            guard let self = self else { return }
            let text = alert.textFields?.first?.text?.trimmingCharacters(in: .whitespaces) ?? ""
            if !text.isEmpty {
                self.serverURL = text
                self.loadServer()
            }
        })
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
        present(alert, animated: true)
    }

    // MARK: - WKNavigationDelegate

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        activityIndicator.stopAnimating()
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        activityIndicator.stopAnimating()
        showConnectionError(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        activityIndicator.stopAnimating()
        showConnectionError(error)
    }

    private func showConnectionError(_ error: Error) {
        let html = """
        <!DOCTYPE html><html><head>
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <style>
          body{background:#111317;color:#e8eaed;font-family:-apple-system,sans-serif;
               display:flex;flex-direction:column;align-items:center;justify-content:center;
               min-height:100vh;text-align:center;padding:24px;margin:0}
          h2{color:#f87171;margin-bottom:12px;font-size:20px}
          p{color:#8a8f9a;font-size:14px;line-height:1.6;margin:4px 0}
          code{background:#1a1d22;padding:3px 8px;border-radius:6px;font-family:monospace}
          button{margin-top:20px;background:#4f9ef8;color:#fff;border:none;padding:10px 24px;
                 border-radius:8px;font-size:14px;cursor:pointer;font-weight:600}
        </style></head><body>
        <h2>Cannot reach server</h2>
        <p>Make sure Lumi DM is running on your PC</p>
        <p>at <code>\(serverURL)</code></p>
        <p style="margin-top:12px">Tap ⚙ to change the server address</p>
        <button onclick="location.reload()">Retry</button>
        </body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    // MARK: - WKUIDelegate (alert support)

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
        present(alert, animated: true)
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(false) })
        alert.addAction(UIAlertAction(title: "OK",     style: .default) { _ in completionHandler(true) })
        present(alert, animated: true)
    }
}
