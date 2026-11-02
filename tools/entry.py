"""PyInstaller 打包入口。

直接用 src/handwrite/main.py 当入口会在运行时把脚本路径塞进 sys.path，
导致「handwrite」包解析异常；所以用这个薄 wrapper 导入真正的 main。
"""

from handwrite.main import main

if __name__ == "__main__":
    raise SystemExit(main())
