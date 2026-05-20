![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.4+-5865F2?logo=discord&logoColor=white)
![Google Calendar API](https://img.shields.io/badge/Google%20Calendar%20API-v3-4285F4?logo=googlecalendar&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)

# School Task Manager

Googleカレンダーをバックエンドとした、Linuxコマンドで管理できる課題管理Discord Bot。

スラッシュコマンド（`/mk`, `/ls`, `/rm` …）で課題を登録・管理し、Googleカレンダーとリアルタイムで同期する。期限前の自動通知・深夜の自動クリーンアップも備える。

---

## コマンド一覧

`<arg>` は必須、`[arg]` は省略可能

### /mk — 課題登録

```
/mk <subject> <name> <due> [time]
```

| 引数      | 説明                                                      |
| --------- | --------------------------------------------------------- |
| `subject` | 科目名（`subjects.json` からオートコンプリート）          |
| `name`    | 課題名                                                    |
| `due`     | 期限（`12/31` / `12月31日` / `明日` / `2025-01-15` など） |
| `time`    | 締切時刻（例: `13:00`、省略時は終日扱い）                 |

### /ls — 一覧表示

```
/ls [filter] [subject] [sort]
```

| 引数      | 値                                | 説明                                                     |
| --------- | --------------------------------- | -------------------------------------------------------- |
| `filter`  | `-t`                              | 本日が期限                                               |
|           | `-tm`                             | 明日が期限                                               |
|           | `-w`                              | 今週が期限                                               |
|           | `-a`                              | すべての未完了（デフォルト）                             |
| `subject` | 科目名                            | 科目でフィルタリング（オートコンプリート）               |
| `sort`    | `id` / `subject` / `name` / `due` | ソートキー（各 `↑ asc` / `↓ desc`、デフォルト: `due ↑`） |

### /cat — 詳細表示

```
/cat [id] [name] [subject]
```

いずれか1つを指定する。

| 引数      | 説明                                   |
| --------- | -------------------------------------- |
| `id`      | IDで1件表示                            |
| `name`    | 課題名で部分一致検索（最大5件）        |
| `subject` | 科目名で一覧表示（オートコンプリート） |

### /edit — 編集

```
/edit <id> [name] [subject] [notes]
```

`name` / `subject` / `notes` のうち少なくとも1つを指定する。

| 引数      | 説明                               |
| --------- | ---------------------------------- |
| `id`      | 編集する課題のID（必須）           |
| `name`    | 新しい課題名                       |
| `subject` | 新しい科目名（オートコンプリート） |
| `notes`   | メモ・説明                         |

### /rm — 削除

```
/rm <id>
```

| 引数 | 説明             |
| ---- | ---------------- |
| `id` | 削除する課題のID |

### /top — 期限順表示

```
/top [count]
```

| 引数    | 説明                        |
| ------- | --------------------------- |
| `count` | 表示件数（デフォルト: `5`） |

### /settings notify — 通知設定

```
/settings notify [timing]
```

| `timing` の値 | 説明                                   |
| ------------- | -------------------------------------- |
| `1d`          | 期日1日前の 20:00 に通知（デフォルト） |
| `2d`          | 期日2日前の 20:00 に通知               |
| `当日`        | 期日当日の 08:00 に通知                |
| `off`         | 通知を無効化                           |
| *(省略)*      | 現在の設定を表示                       |

### その他

| コマンド         | 引数        | 説明                                                     |
| ---------------- | ----------- | -------------------------------------------------------- |
| `/ping`          | —           | Botのレイテンシを確認する                                |
| `/reboot`        | —           | Botを再起動する（管理者専用）                            |
| `/admin-comment` | `<message>` | 管理者からのお知らせをチャンネルに送信する（管理者専用） |
| `/gitlatest`     | —           | GitHubリポジトリの最新コミット情報を表示する             |
| `/man`           | —           | マニュアルを表示する                                     |

---

## セットアップ

### 1. Discord Bot の作成

1. [Discord Developer Portal](https://discord.com/developers/applications) を開き、**New Application** でアプリを作成する。
2. 左メニューの **Bot** を開き、**Add Bot** をクリック。
3. **Token** セクションの **Reset Token** でトークンを発行し、コピーする（`DISCORD_TOKEN`）。
4. 同ページの **Privileged Gateway Intents** で以下を有効化する。
   - **Server Members Intent**
   - **Message Content Intent**
5. 左メニューの **OAuth2 > URL Generator** を開き、**Scopes** で `bot` と `applications.commands` を選択する。
6. **Bot Permissions** で `Send Messages` / `Read Message History` / `View Channels` を選択する。
7. 生成されたURLをブラウザで開き、Botを招待するサーバーを選択する。

### 2. リポジトリをクローン

```sh
git clone https://github.com/<your-username>/SchoolTaskManager.git
cd SchoolTaskManager
```

### 3. 依存パッケージのインストール

```sh
pip install -r requirements.txt
```

### 4. 環境変数の設定

`.env` ファイルをプロジェクトルートに作成する。

```env
DISCORD_TOKEN=your_discord_bot_token
DISCORD_GUILD_ID=your_discord_server_id
DISCORD_CHANNEL_ID=your_discord_channel_id
DISCORD_ADMIN_IDS=your_discord_user_id

GOOGLE_CREDENTIALS_JSON={"type":"service_account",...}
GOOGLE_CALENDAR_ID=your_calendar_id@group.calendar.google.com

TIMEZONE=Asia/Tokyo
NOTIFY_LEAD=1d

# Optional
GITHUB_REPO=owner/repo
GITHUB_TOKEN=ghp_xxxxxxxxxxxx
```

| 変数                      | 必須 | 説明                                                         |
| ------------------------- | ---- | ------------------------------------------------------------ |
| `DISCORD_TOKEN`           | ✅    | Discord Bot トークン                                         |
| `DISCORD_GUILD_ID`        | ✅    | 対象サーバーのID                                             |
| `DISCORD_CHANNEL_ID`      | ✅    | 通知を送るチャンネルのID                                     |
| `DISCORD_ADMIN_IDS`       |      | 管理者ユーザーIDをカンマ区切りで指定。未設定時は全員が `/reboot` , /admin-comment` を使用不可 |
| `GOOGLE_CREDENTIALS_JSON` | ✅    | サービスアカウントの認証情報（JSON文字列）                   |
| `GOOGLE_CALENDAR_ID`      | ✅    | 課題を登録するカレンダーのID                                 |
| `TIMEZONE`                |      | タイムゾーン（デフォルト: `Asia/Tokyo`）                     |
| `NOTIFY_LEAD`             |      | 通知タイミング（`1d` / `2d` / `当日` / `off`、デフォルト: `1d`） |
| `GITHUB_REPO`             |      | `/gitlatest` で使用するリポジトリ（例: `owner/repo`）        |
| `GITHUB_TOKEN`            |      | GitHub API のトークン（プライベートリポジトリ向け）          |

### 5. 科目リストの作成

`subjects.json.example` をコピーして編集する。

```sh
cp subjects.json.example subjects.json
```

```json
["数学", "英語", "物理", "化学"]
```

### 6. Botの起動

```sh
python main.py
```

---

## Dockerで起動する

```sh
docker compose up -d
```

コンテナは `restart: always` で起動するため、`/reboot` コマンドによる再起動も自動で復帰する。

---

## 日付フォーマット

`/mk` の `due` 引数では以下の形式が使用できる。

```
今日 / today
明日 / tomorrow
明後日
12/31
12月31日
2025-01-15
```

時刻指定は `time` 引数で別途指定する。

```
13:00
```

---

## 自動通知・クリーンアップ

- **通知**: 設定したタイミング（デフォルト: 期日1日前の20:00）に指定チャンネルへリマインドを送信する。
- **自動クリーンアップ**: 毎日00:00に、期限切れとなった課題をGoogleカレンダーから自動削除し、チャンネルに送信（非通知）する。
