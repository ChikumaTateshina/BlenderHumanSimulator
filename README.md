# BlenderHumanSimulator

Blenderのシーン内を、人の目線の高さで一人称視点のまま歩き回って確認できるアドオンです。建築物や展示空間などを制作しながら、実際のスケール感・動線・天井高を確かめる用途を想定しています。

A Blender add-on for first-person walkthroughs of your scene at human eye level, with capsule collision, steps, slopes, jumping and crouching.

## 特徴

- **一人称ウォークスルー**: 3Dカーソルの位置に立ち、専用ウィンドウで歩き回る（アバターは表示しない）
- **人の身体に近い衝突判定**: 半径0.20 mのカプセルで、壁・床・天井と衝突する。0.30 m以下の段差は自動で昇降し、45°までの斜面を歩ける
- **姿勢変更**: 立ち（視点1.60 m）、しゃがみ（55%）、地面付近（15%）を切り替えられる。天井が低い場所では立ち上がらない
- **非破壊**: Collision や Rigid Body をオブジェクトに付けない。衝突データは実行時のメモリ上にだけ作る
- **編集しながら確認**: 一時停止中に元のウィンドウでモデルを編集し、「Rebuild Collisions」で衝突判定に反映できる
- **単位系に対応**: シーンの Unit Scale、非一様スケール、負スケールのメッシュを考慮する

## 動作環境

- Blender 4.2 LTS 以降（4.2.2 / 5.2.0 で自動テスト済み）
- Windows で開発。カーソルをウィンドウ内に閉じ込める機能は Windows のみ

## インストール

1. リポジトリのルートで、アドオンの zip をビルドする。

   ```
   blender -c extension build --source-dir blender_walkthrough --output-dir dist
   ```

2. Blender の `Edit > Preferences > Get Extensions > 右上の ▼ > Install from Disk...` で、`dist/` にできた zip を選ぶ。
3. 3Dビューのサイドバー（Nキー）に「Walkthrough」タブが表示される。

## 使い方

1. 3Dカーソルを床の上に置く（Shift+右クリック）。
2. サイドバー「Walkthrough」タブで **Start Walkthrough** を押すと、専用ウィンドウが開く。
3. 専用ウィンドウのビュー上で、次のキーで操作する。

| 入力 | 動作 |
|---|---|
| W / A / S / D | 前後左右に移動 |
| Shift（押している間） | ダッシュ |
| Space | ジャンプ（接地中のみ） |
| C | しゃがみ（腰の高さ）の切り替え |
| Z | 地面付近の視点の切り替え |
| マウス | 視線の操作 |
| P または Pause ボタン | 一時停止 / 再開（一時停止中はマウスが自由になる） |
| Esc または Stop ボタン | 終了 |

### 編集しながら確認する

1. **P** で一時停止する。
2. 元のウィンドウでモデルを編集する。
3. **Rebuild Collisions** を押して、衝突データを作り直す。
4. **P** で再開する。

作り直すまでは、衝突判定は開始時の形状のまま変わりません。画面の表示は編集後の状態になります。

### 設定（サイドバー）

| 項目 | 既定値 |
|---|---|
| Eye Height | 1.60 m |
| Walk Speed / Run Speed | 3.0 / 5.0 m/s |
| Mouse Sensitivity | 0.05 °/px |
| Head Clearance | 0.10 m |

サイドバーには、衝突対象のオブジェクト数、三角形数、BVH構築時間、接地状態、エラーなどの診断情報も表示されます。

## 制限事項

- 衝突は、移動を細かく分割して判定する近似方式です（厳密な連続衝突判定ではありません）。
- カーブ・テキストなどのメッシュ以外のオブジェクトや、ジオメトリノード・パーティクルのインスタンスは衝突しません。対象外になったものは診断情報に表示されます。
- 空中では横方向の勢いが残ります。摩擦は再現していません。
- 座標値が非常に大きいシーンでは、接触の精度が下がります。
- VRヘッドセット、アバター表示、ネットワーク同期には対応していません。

## 開発

テストは Blender 上で実行します。

```
blender -b --factory-startup --python tests/run_tests.py
```

環境変数 `WT_TEST_PATTERN`（例: `test_physics.py`）で、実行するテストを絞り込めます。

| モジュール | 役割 |
|---|---|
| `physics.py` | カプセルと三角形の衝突判定、段差・斜面・壁沿いの移動（bpy に依存しない） |
| `player.py` | 視点、姿勢、ジャンプ、固定刻みでの更新（bpy に依存しない） |
| `collision_world.py` | 可視メッシュのスナップショット作成、BVH、グリッドによる空間検索 |
| `session.py` | 状態管理と、モーダル入力の処理 |
| `windowing.py` / `viewport.py` | 専用ウィンドウの生成、ビューの反映と復元 |
| `input.py` / `cursor_clip.py` | キー・マウス入力、カーソルの閉じ込め |
| `operators.py` / `ui.py` / `properties.py` | オペレーター、サイドバーUI、設定 |

## ライセンス

[MIT License](LICENSE)
