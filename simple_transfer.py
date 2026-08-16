import os
import subprocess
import glob

# Configuration
SOURCE_DIR = "./captures"
SERVER_IP = "100.126.82.18"
SERVER_USER = "Acer"
DEST_DIR = r"d:\POC project\incoming"

def main():
    if not os.path.exists(SOURCE_DIR):
        print(f"Error: Source directory '{SOURCE_DIR}' does not exist.")
        return

    # Find all files in captures directory
    files = glob.glob(os.path.join(SOURCE_DIR, "*"))
    if not files:
        print("No files found to transfer.")
        return

    print(f"Found {len(files)} files. Starting transfer to {SERVER_IP}...")
    
    dest_target = f"{SERVER_USER}@{SERVER_IP}:{DEST_DIR}"

    for filepath in files:
        if os.path.isdir(filepath):
            continue
            
        filename = os.path.basename(filepath)
        print(f"Transferring {filename}...", end=" ", flush=True)
        
        # SCP command (accepting host key automatically)
        cmd = ["scp", "-o", "StrictHostKeyChecking=no", filepath, f"{dest_target}\\{filename}"]
        result = subprocess.run(cmd, capture_output=True)
        
        if result.returncode == 0:
            print("✅ Success")
        else:
            print(f"❌ Failed: {result.stderr.decode().strip()}")

if __name__ == "__main__":
    main()
