from pathlib import Path

path = Path(r"c:/Users/sistemas5/Desktop/Mis cosas/scanPrinter/scanTk.py")
path.write_text(
    'from scanner_ui import main\n\n\nif __name__ == "__main__":\n    main()\n',
    encoding="utf-8",
)
print(path.read_text(encoding="utf-8"))
