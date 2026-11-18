# Local Trans 技術メモ

開発者向けの情報です。使い方は [README](../README.md) を見てください。

## コンソールを出して動かす（デバッグ用）

`LocalTrans.bat` とショートカットは `pythonw.exe` で起動するので、コンソールが出ません。エラーや print の出力は画面に出ず、`local-trans.log` にだけ残ります。

コードを書き換えて試すときや、起動してすぐ落ちる原因を見たいときは、次のコマンドで起動します。`python.exe` で動くので、出力がそのままターミナルに出ます。`uv run` は起動のたびに依存パッケージを `pyproject.toml` と照らし合わせ、足りないものを入れてから起動します。

```bash
uv run python main.py
```

デスクトップとスタートメニューのショートカットは `uv run python tools/make_shortcuts.py` で作り、`--remove` で消します。

## 構成

- `main.py`: 起動処理。AppUserModelID の設定、QApplication とメインウィンドウの作成
- `localtrans/capture.py`: 画面キャプチャ（dxcam → mss）
- `localtrans/ocr.py`: Windows 標準 OCR（Windows.Media.Ocr, en-US）と段落の組み立て
- `localtrans/translator.py`: PLaMo 3 Translate 0.8B（transformers, bf16, CUDA）
- `localtrans/hotkeys.py`: グローバルショートカット（RegisterHotKey＋キー状態の監視）
- `localtrans/winutil.py`: ウィンドウの前面化など Win32 の補助
- `localtrans/presets.py`: 範囲プリセットの管理と範囲の枠表示
- `localtrans/selector.py`: 範囲選択のオーバーレイ
- `localtrans/compact.py`: 最小表示の帯
- `localtrans/worker.py`: OCR と翻訳のバックグラウンド処理、自動監視
- `localtrans/window.py`: メインウィンドウ
- `localtrans/config.py`: 設定の読み書き
- `localtrans/settings_dialog.py`: 設定画面
- `tools/make_shortcuts.py`: デスクトップとスタートメニューのショートカットを作る

設定は `config.json`、ログは `local-trans.log` に保存します。どちらも Git の管理から外しています。

## 画面キャプチャ

- DXGI Desktop Duplication（dxcam）で撮ります。DirectX のフルスクリーンゲームを撮っても黒くなりません。
- dxcam で取れなかったときは mss（GDI）で撮り直します。

## ショートカットの受け取り

ショートカットは 2 通りの方法で受け取ります。

1. 通常のホットキー登録（RegisterHotKey）
2. キーの押下状態の監視（GetAsyncKeyState のポーリング）

入力を独占するゲーム（DirectInput など）では RegisterHotKey にキーが届かないことがあります。そのときも監視のほうで反応します。

`local-trans.log` には、ショートカットをどちらの方法で受け取ったか（hotkey / poll）、そのとき前面にあったウィンドウ、選択画面を前に出せたかを記録しています。

## フルスクリーンのゲームへの対応

- 範囲選択の画面は、ゲームが前面にあっても強制的に前に出してフォーカスを取ります。選び終わると、ゲームを前面に戻します。
- 選択画面がゲームの裏に隠れて操作できなくなったときは、同じショートカットをもう一度押すと閉じます。
- 翻訳の結果が出ても、翻訳ウィンドウはフォーカスを奪いません。
- 排他フルスクリーンでは、Windows の仕組み上、ほかのウィンドウをゲームの上に出せません。

## 最小表示の帯

`localtrans/compact.py` の `CompactView` です。

- 枠なし・最前面・Tool ウィンドウで、`WindowDoesNotAcceptFocus` と `WA_ShowWithoutActivating` を付けています。帯をクリックしても、前面のウィンドウは変わりません。
- 「クリックを透過する」は `WindowTransparentForInput` で実装しています。Qt はウィンドウのフラグを変えるとウィンドウをいったん隠すので、変えたあとに表示し直しています。
- 最小表示のあいだはメインウィンドウを隠すので、`setQuitOnLastWindowClosed(False)` にしています。アプリはメインウィンドウの `closeEvent` で終了します。

## タスクバーのアイコン

- `main.py` で `SetCurrentProcessExplicitAppUserModelID("LocalTrans.App")` を呼び、タスクバーで Python とは別のアプリとして扱われるようにしています。
- `tools/make_shortcuts.py` は、ショートカットにも同じ AppUserModelID（PKEY_AppUserModel_ID）とアイコン（`assets/icon.ico`）を設定します。起動先は `.venv\Scripts\pythonw.exe` です。
