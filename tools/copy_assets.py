import os
import shutil

def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    src_dir = os.path.join(root, 'Resouces')
    icons_dir = os.path.join(root, 'browser-extension', 'icons')
    static_dir = os.path.join(root, 'static')
    os.makedirs(icons_dir, exist_ok=True)
    os.makedirs(static_dir, exist_ok=True)

    mapping = {
        'my_logo.png': ['icon16.png', 'icon48.png', 'icon128.png'],
        'background.png': ['background.png']
    }

    for src_name, targets in mapping.items():
        src_path = os.path.join(src_dir, src_name)
        if not os.path.exists(src_path):
            print(f'MISSING: {src_path}')
            continue
        for t in targets:
            if t.startswith('icon'):
                dst = os.path.join(icons_dir, t)
            else:
                dst = os.path.join(static_dir, t)
            try:
                shutil.copyfile(src_path, dst)
                print(f'Copied {src_path} -> {dst}')
            except Exception as e:
                print(f'Failed to copy {src_path} -> {dst}: {e}')

if __name__ == '__main__':
    main()
