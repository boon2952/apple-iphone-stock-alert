# Apple日本 認定整備済iPhone 在庫通知

PythonとGitHub Actionsで、Apple日本公式の https://www.apple.com/jp/shop/refurbished/iphone を確認する仕組みです。購入・カート投入・ログインは一切行いません。LINE未設定でも使えます。

**検証状況：この開発環境ではAppleへのネットワーク接続が許可されておらず、現在のHTML・在庫・robots.txt・利用条件は実地確認できていません。構造化データと購入ボタンを使う保守的な実装であり、実サイト対応済み・購入可能商品ありとは保証できません。形式が合わなければ明示的に失敗します。** テストは人工的なHTMLによるものです。

## 監視条件

| 対象 | モデル | 容量 | 税込価格 |
| --- | --- | --- | --- |
| 本人用 | iPhone 15以降の無印・e | 256GB以上 | 100,000円以下 |
| 母親用 | iPhone 15以降の無印・e | 128GB以上 | 100,000円以下 |

Pro・Pro Max・Plus・Air・mini・SEを除外します。色ごとに商品URLを区別し、両方の条件に合う商品は1件にまとめます。URLのクエリは除きます。

## 最初にGitHubで行う操作

1. この変更のプルリクエスト（PR＝変更内容の提案）を開き、`Files changed` で内容を確認します。
2. `Checks` のTestsが成功したことを確認し、`Merge pull request` → `Confirm merge` を押します。これでmainに反映されます。
3. Appleの[利用条件](https://www.apple.com/jp/legal/internet-services/terms/site.html)を読み、自動アクセスが認められるか確認してください。robots.txtの許可は利用規約上の許可を意味しません。自動アクセスが認められない場合は監視を有効にしないでください。規約が禁止する場合、この方式は利用できません。
4. 一時停止するには `Actions` → `iPhone stock monitor` → 右上の `…` → `Disable workflow`。再開は `Enable workflow` です。定期実行はmainへの反映後に有効になるため、利用条件確認前にマージしないでください。
5. `Settings` → `Actions` → `General` → `Workflow permissions` を確認します。組織のポリシーで書き込みが禁止されていると状態保存に失敗します。このworkflowは `contents: write` を要求します。
6. `Actions` → `iPhone stock monitor` → `Run workflow` → Branchをmain、modeを `dry-run` にして実行します。LINE Secretsは不要です。
7. 実行を開き、`Check Apple and notify` のログを読みます。成功なら対象商品一覧が出ます。`Artifacts` の `monitor-report-...` もダウンロードできます。赤い失敗は「在庫なし」ではありません。
8. 次にmodeを `monitor` にして手動実行します。最初の**成功した**監視は基準状態の保存だけで、既存在庫の通知はしません。その後は5分間隔の予定で動きます。

GitHubのスケジュールは厳密な5分おきの保証がありません。混雑で遅延・欠落することがあります。スケジュールはデフォルトブランチ上のみ実行され、公開リポジトリでは60日間活動がないと停止する場合があります。Actionsの無料枠・料金も確認してください。

## 在庫をどう判断するか

毎回robots.txt → 商品一覧 → 一覧にリンクがあるiPhoneの商品詳細を順番にGETします。通常2秒以上空け、robotsのCrawl-delayが長ければ従います。最大100商品までで、余分な在庫API・アクセス制限回避・プロキシ切替・自動再試行は使いません。

詳細のJSON-LDに一意のProduct、容量、JPY価格、`InStock` があり、ページ内に税込表示と有効な「バッグに追加／カートに追加」ボタンがある場合だけ対象とします。`OutOfStock` 等は在庫なしとします。条件が不足したページや形式変更は不明として監視全体を失敗させます。一覧に商品がない場合も明示的な在庫なし文言が必要です。購入ボタンは読み取るだけです。配送先別の配送可能性・最終購入成功・カート在庫確保までは確認できません。

確認失敗では前回の正常な在庫状態を更新せず、LINEも送りません。失敗理由はActionsログとreportに残ります。403・429・リダイレクト・タイムアウト・robots拒否があれば停止し、回避しません。継続して失敗する場合はworkflowを無効化してください。自動化を禁止する利用条件には従ってください。

## 状態保存と重複防止

`monitor-state` ブランチの `state.json` に、前回の対象在庫と保留通知を保存します。期限付きキャッシュは使いません。このブランチを削除すると次回が初回扱いになるため削除しないでください。公開リポジトリでは商品と検知日時は公開されますが、LINEのトークンや送信先は保存しません。

前回なかった商品が購入可能になった場合に新規／再入荷として通知します。通常の在庫継続や価格変更だけでは再通知しません。一度正常に在庫なしを確認した後の復活は再通知します。監視失敗中の売り切れ・再入荷や、5分の間の変化は検知できません。

LINE未設定では入荷イベントを保留します。次の正常監視でも購入可能な商品のみ再送対象にし、売り切れた保留は削除します。LINE失敗でも状態を保存します。再送には同一の `X-Line-Retry-Key` を使います。LINEの再送キー有効期間（24時間）を超えて重複送信しないよう、23時間を超えた保留は自動再送せずエラーにします。

期限超過時はworkflowを停止し、LINE側で受信有無を確認してから、GitHubで `monitor-state` ブランチの `state.json` を開き、編集ボタンで `pending` を `{}` にして保存してください。これは保留通知を破棄する操作です。在庫の `stock` は残してください。その後workflowを再開します。長期間LINE未設定だった場合も同じ対応です。LINE有効化時に現在の全在庫を通知する機能はありません。

状態保存のpushが失敗した実行は必ず確認してください。LINE送信とGitHub保存を一つのトランザクションにはできないため、保存障害を含む完全な「必ず一回だけ」の配送は保証できません。同時実行はconcurrencyで直列化し、状態の取得失敗・不正ファイルを初回として扱いません。

## LINEを後から設定する

今は何も入力する必要はありません。LINE Notify（終了済み）ではなくMessaging APIを使います。

1. [LINE公式アカウント](https://www.lycbiz.com/jp/service/line-official-account/)を作り、LINE Official Account ManagerでMessaging APIを有効にします。
2. [LINE Developers](https://developers.line.biz/console/)でそのMessaging APIチャネルを開きます。Messaging APIタブからチャネルアクセストークンを発行します。
3. その公式アカウントを自分のLINEで友だち追加し、ブロックしていないことを確認します。
4. 自分への送信ならチャネルの基本設定にある自分のユーザーIDを確認します。通常 `U...` のIDで、表示名や一般のLINE IDとは異なります。グループ等はWebhookで取得する正式な送信先IDが必要です。本システムにはWebhookサーバーは含みません。
5. GitHubリポジトリの `Settings` → `Secrets and variables` → `Actions` → `New repository secret` から以下を登録します。

| Name | Secret |
| --- | --- |
| `LINE_CHANNEL_ACCESS_TOKEN` | チャネルアクセストークン |
| `LINE_TO` | 通知先のユーザーID等 |

両方設定すると次のmonitor実行から送信します。片方だけではエラーになります。コード・Issue・PR・ログに認証情報を書かないでください。通知には商品名・容量・税込価格・購入URL・日本時間の検知日時を含みます。Messaging APIの送信条件、無料枠、月間上限やプラン料金は[公式ドキュメント](https://developers.line.biz/ja/docs/messaging-api/sending-messages/)で確認してください。受信確認のために実在庫を偽装しないでください。

## 自分のPCで確認する（任意）

Python 3.12以降をインストールし、リポジトリをダウンロードしてそのフォルダで実行します。

```sh
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python monitor.py --dry-run
```

activateは自分のOSの1行だけを実行してください。dry-runはAppleにアクセスしますが、LINE送信と状態変更は行わず、`report.json` のみ出力します。`python monitor.py` はローカルの `state.json` に保存します（GitHubの状態とは別です）。ネットワークなしの単体テストは人工HTMLとモックを使い、LINE Secretsも実通信も不要です。

## エラーの見方

- Apple HTTP 403 / 429、robots拒否：監視停止。制限を回避しないでください。
- 商品・容量・価格・税込表示・購入ボタンが確認できない：ページ形式変更または未対応。購入可能とは判定しません。コード修正と実HTMLによる再検証が必要です。
- LINE HTTP 401 / 403：トークンやチャネル・送信先条件を確認。429は上限や制限を確認。
- 状態復元／push失敗：Actionsの書き込み権限、ブランチ保護、リポジトリルールを確認。保存先専用ブランチへの書き込みが必要です。
- 監視失敗通知はLINEには送りません。GitHubのActions通知設定を有効にし、失敗ログとArtifactsを確認してください。

## ファイル

`monitor.py` が監視本体、`tests/test_monitor.py` が通信なしのテスト、`.github/workflows/monitor.yml` が定期実行、`test.yml` がPR等のテストです。LINE認証情報は環境変数からのみ読み取ります。
