from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys


def main() -> int:
    project_dir = Path(__file__).resolve().parent
    data_dir = project_dir / "data"
    parquet = data_dir / "vm_skus_all_regions.parquet"
    now = datetime.now(timezone.utc)
    print("Azure VM SKU Explorer")
    print(f"Current time: {now.isoformat(timespec='seconds')}")
    for name in ("vm_skus_all_regions.csv", "vm_skus_all_regions.parquet"):
        path = data_dir / name
        if not path.is_file():
            print(f"  {name}: not present")
            continue
        info = path.stat()
        modified = datetime.fromtimestamp(info.st_mtime, timezone.utc)
        age_seconds = int((now - modified).total_seconds())
        age = str(timedelta(seconds=abs(age_seconds)))
        age_description = f"{age} old" if age_seconds >= 0 else f"{age} in the future"
        print(
            f"  {name}: {info.st_size:,} bytes; "
            f"last modified {modified.isoformat(timespec='seconds')}; age: {age_description}"
        )

    if parquet.is_file():
        print("Saved data is available for the SKU browser.")
    else:
        print("The SKU browser requires a Parquet data file. Fetch new data first.")

    print("\n1. Fetch new Azure VM SKU data")
    print("2. Open the SKU browser (Streamlit)")
    print("Q. Quit")
    while True:
        try:
            choice = input("Choose an option [1/2/Q]: ").strip().lower()
        except EOFError:
            print("No selection received; nothing started.", file=sys.stderr)
            return 1
        if choice == "q":
            return 0
        if choice == "1":
            command = [sys.executable, str(project_dir / "fetch_vm_skus_all_regions.py")]
            break
        if choice == "2":
            if not parquet.is_file():
                print("Cannot open the SKU browser: Parquet data is missing. Choose 1 to fetch it.")
                continue
            command = [
                sys.executable, "-m", "streamlit", "run",
                str(project_dir / "streamlit_app.py"),
            ]
            break
        print("Please choose 1, 2, or Q.")

    result = subprocess.run(command, cwd=project_dir)
    if result.returncode:
        print(f"The selected command exited with code {result.returncode}.", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
