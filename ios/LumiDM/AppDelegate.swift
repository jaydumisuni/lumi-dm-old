import UIKit

@UIApplicationMain
class AppDelegate: UIResponder, UIApplicationDelegate {

    var window: UIWindow?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {

        // Init engine on launch so state loads before any UI appears
        _ = DownloadEngine.shared
        NotificationHelper.shared.requestPermission()

        let accent = UIColor(red: 0.310, green: 0.620, blue: 0.973, alpha: 1)
        let bg     = UIColor(red: 0.102, green: 0.114, blue: 0.133, alpha: 1)
        let dark   = UIColor(red: 0.067, green: 0.075, blue: 0.090, alpha: 1)
        let text   = UIColor(red: 0.910, green: 0.918, blue: 0.929, alpha: 1)

        // ── Tab bar ────────────────────────────────────────────────────────────

        let downloadsVC  = nav(DownloadsViewController(),
                               title: "Downloads",
                               image: UIImage(systemName: "arrow.down.circle"),
                               selected: UIImage(systemName: "arrow.down.circle.fill"))

        let addVC        = nav(NewDownloadViewController(),
                               title: "Add",
                               image: UIImage(systemName: "plus.circle"),
                               selected: UIImage(systemName: "plus.circle.fill"))

        let settingsVC   = nav(SettingsViewController(),
                               title: "Settings",
                               image: UIImage(systemName: "gearshape"),
                               selected: UIImage(systemName: "gearshape.fill"))

        let tabBar = UITabBarController()
        tabBar.viewControllers = [downloadsVC, addVC, settingsVC]

        // Style
        let tabAppearance = UITabBarAppearance()
        tabAppearance.configureWithOpaqueBackground()
        tabAppearance.backgroundColor = bg
        tabAppearance.stackedLayoutAppearance.normal.iconColor    = UIColor(red: 0.540, green: 0.560, blue: 0.604, alpha: 1)
        tabAppearance.stackedLayoutAppearance.normal.titleTextAttributes    = [.foregroundColor: UIColor(red: 0.540, green: 0.560, blue: 0.604, alpha: 1)]
        tabAppearance.stackedLayoutAppearance.selected.iconColor   = accent
        tabAppearance.stackedLayoutAppearance.selected.titleTextAttributes  = [.foregroundColor: accent]
        tabBar.tabBar.standardAppearance    = tabAppearance
        tabBar.tabBar.scrollEdgeAppearance  = tabAppearance

        let navAppearance = UINavigationBarAppearance()
        navAppearance.configureWithOpaqueBackground()
        navAppearance.backgroundColor       = bg
        navAppearance.titleTextAttributes   = [.foregroundColor: text]
        UINavigationBar.appearance().standardAppearance   = navAppearance
        UINavigationBar.appearance().compactAppearance    = navAppearance
        UINavigationBar.appearance().scrollEdgeAppearance = navAppearance
        UINavigationBar.appearance().tintColor            = accent

        window = UIWindow(frame: UIScreen.main.bounds)
        window?.backgroundColor    = dark
        window?.rootViewController = tabBar
        window?.makeKeyAndVisible()
        return true
    }

    func application(_ app: UIApplication,
                     open url: URL,
                     options: [UIApplication.OpenURLOptionsKey: Any] = [:]) -> Bool {
        handleSharedURL(url.absoluteString)
        return true
    }

    func application(_ application: UIApplication,
                     continue userActivity: NSUserActivity,
                     restorationHandler: @escaping ([UIUserActivityRestoring]?) -> Void) -> Bool {
        if let url = userActivity.webpageURL {
            handleSharedURL(url.absoluteString)
        }
        return true
    }

    private func handleSharedURL(_ urlStr: String) {
        guard let tab = window?.rootViewController as? UITabBarController,
              let nav = tab.viewControllers?[1] as? UINavigationController,
              let vc  = nav.viewControllers.first as? NewDownloadViewController else { return }
        vc.prefillURL = urlStr
        tab.selectedIndex = 1
    }

    // MARK: - Helper

    private func nav(_ root: UIViewController,
                     title: String,
                     image: UIImage?,
                     selected: UIImage?) -> UINavigationController {
        root.title = title
        root.tabBarItem = UITabBarItem(title: title, image: image, selectedImage: selected)
        return UINavigationController(rootViewController: root)
    }
}
