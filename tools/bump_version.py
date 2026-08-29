import sys
import re
import os

def main():
    bump_type = sys.argv[1].lower() if len(sys.argv) > 1 else "patch"
    
    # プロジェクトルートの core/version.py を対象とする
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    version_file = os.path.join(project_root, "core", "version.py")
    
    if not os.path.exists(version_file):
        print(f"Error: {version_file} not found.")
        sys.exit(1)
        
    with open(version_file, "r", encoding="utf-8") as f:
        content = f.read()
        
    match = re.search(r'APP_VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', content)
    if not match:
        print("Error: Version string not found in version.py.")
        sys.exit(1)
        
    major, minor, patch = map(int, match.groups())
    
    if bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    elif bump_type == "minor":
        minor += 1
        patch = 0
    else:
        patch += 1
        
    new_version = f"{major}.{minor}.{patch}"
    
    new_content = re.sub(r'APP_VERSION\s*=\s*"\d+\.\d+\.\d+"', f'APP_VERSION = "{new_version}"', content)
    with open(version_file, "w", encoding="utf-8") as f:
        f.write(new_content)
        
    # バッチファイルで受け取るために標準出力にバージョンのみを出力
    print(new_version)

if __name__ == "__main__":
    main()
