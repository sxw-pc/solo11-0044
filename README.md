# 帧率探针（fpsprobe）

> **合规声明**
>
> 本工具仅用于分析**使用者本人所有，或已获得明确授权**的本地视频文件。本工具完全离线运行：不联网、不上传、不解码视频画面、不提供转码或任何规避检测能力。仓库内的示例视频由本地脚本以确定性方式合成，不包含第三方素材。请勿将本工具用于分析无权处理的文件。

**一句话定位**：在本地批量读取 MP4 元数据与逐帧时间戳，核验“容器声明的帧率”和“时间戳实际表现”是否一致，并把可复现的结论写入 SQLite 台账、JSON/Markdown 报告和只读 HTTP API。

### 是 / 不是

| 是 | 不是 |
| --- | --- |
| 本地文件批处理工具 | 不能在线拉取素材或访问远端服务 |
| 只读解析 MP4 元数据与 `stts`/`ctts` 时间表 | 不解码像素、不算画面质量、不做比对 |
| 输出可解释的四类时间基准判定 | 不“修复”视频、不转码、不重封装 |
| 提供台账与只读 JSON API | 不提供 Web 前端、看板、登录或上传 |
| 适合入库前卡点、剪辑前整理、数据清洗 | 不支持 MPEG-TS、WebM、MKV、AVI |

---

## 快速上手

要求 Python 3.10+。运行期没有第三方依赖；测试使用 pytest。

```bash
# 1. 生成 5 个确定性 fixture（每次运行 sha256 相同）
python -m fpsprobe fixtures gen

# 2. 整批分析、写入 SQLite、生成 JSON + Markdown 报告
python -m fpsprobe batch --config fixtures/config/default.yaml

# 3. 启动只读本地服务（默认只监听 127.0.0.1:5180）
python -m fpsprobe serve --port 5180
```

说明：仓库自带的 fixture 中有一个故意构造的 `TIMESTAMP_BROKEN` 文件，因此 `batch` 的退出码为 `1` 是预期结果，表示该批次中存在 `reject`。若只想看单文件结果：

```bash
python -m fpsprobe probe fixtures/out/vfr_jitter.mp4
```

该命令会输出结构化 JSON，其中 `verdict` 为 `VFR_SUSPECT`。服务启动后可以访问：

```bash
curl http://127.0.0.1:5180/health
```

也可以使用：

```bash
make test    # pytest -q
make lint    # ruff（存在时），否则 python -m compileall
make demo    # 生成 fixture -> 整批分析 -> 打印摘要和报告路径
```

`make demo` 允许批处理退出码为 0 或 1（fixture 中有 reject）。

---

## 配置文件

默认配置为 `fixtures/config/default.yaml`；`fixtures/config/default.json` 是在没有 PyYAML 时使用的等价 JSON 回退；`fixtures/config/strict.json` 是更严格阈值的示例。

**YAML/JSON 回退规则**：请求 `.yaml`/`.yml` 时优先使用 PyYAML；若 PyYAML 不可用，读取同路径同名的 `.json`。请求 `.json` 时直接读取 JSON。缺少必填字段会返回稳定错误码 `CONFIG_MISSING_FIELD`。

配置中的相对路径由 CLI 按**当前工作目录**解析。推荐从仓库根目录运行。

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `input_dirs` | list[str] | `["fixtures/out"]` | 输入目录；可多个 |
| `recursive` | bool | `true` | 是否递归扫描子目录 |
| `include_suffixes` | list[str] | `.mp4/.m4v/.mov` | 参与扫描的扩展名 |
| `ignore_globs` | list[str] | 隐藏文件与 `*.tmp` | 忽略模式，匹配相对路径或文件名 |
| `primary_track` | str/int | `first_video` | 主力视频轨策略：`first_video`、`longest_video` 或视频轨的 0 基索引 |
| `max_detail_frames` | int | `240` | 逐帧明细入库阈值；超过后 `timeline_samples` 只存摘要 |
| `histogram_buckets` | int | `8` | delta 直方图最大桶数 |
| `adapter` | str | `builtin` | `builtin`（默认）或可选的 `ffprobe` |
| `report.output_dir` | str | `workdir` | 报告目录 |
| `report.json_name` | str | `report.json` | JSON 报告文件名 |
| `report.markdown_name` | str | `report.md` | Markdown 报告文件名 |
| `database.path` | str | `workdir/fpsprobe.db` | SQLite 台账路径 |
| `thresholds.fps_abs_tolerance` | float | `0.01` | 声明/实测 FPS 绝对容差 |
| `thresholds.fps_rel_tolerance` | float | `0.0005` | 声明/实测 FPS 相对容差 |
| `thresholds.cfr_delta_rel_tolerance` | float | `0.01` | delta 相对中位数的 CFR 稳定容差 |
| `thresholds.vfr_jitter_threshold` | float | `0.08` | 抖动系数阈值 |
| `thresholds.vfr_p95_p50_threshold` | float | `1.5` | p95/p50 比值阈值 |
| `thresholds.mismatch_review_abs_fps` | float | `0.02` | CFR_MISMATCH 升级为 review 的 FPS 偏差阈值 |
| `classification.cfr_ok` | str | `ok` | 判定到分档的映射 |
| `classification.cfr_mismatch` | str | `review` | 同上 |
| `classification.vfr_suspect` | str | `review` | 同上 |
| `classification.timestamp_broken` | str | `reject` | 同上 |
| `server.host` | str | `127.0.0.1` | 仅允许回环地址 |
| `server.port` | int | `5180` | 本地 API 端口 |

所有判定阈值均由配置注入，分析代码中没有写死阈值。

---

## 判定标准

工具只读取容器与时间表，不解码素材。每轨 `stts` 给出解码时间间隔，`ctts`（版本 0/1）给出组合偏移，最终按 `PTS = DTS + composition_offset` 得到逐帧时间戳。容器声明帧率按主力视频轨的样本数除以 `mvhd` 影片时长计算。

| 判定 | 触发条件 | 默认分档 |
| --- | --- | --- |
| `CFR_OK` | 所有 delta 为正，且相对中位数偏差在 `cfr_delta_rel_tolerance` 内；实测帧率与声明帧率在绝对/相对容差内一致 | `ok` |
| `CFR_MISMATCH` | 间隔稳定，但声明帧率与实测帧率的绝对差**且**相对差都超过容差 | `review`；FPS 偏差小于 `mismatch_review_abs_fps` 时降为 `ok` |
| `VFR_SUSPECT` | 抖动系数超过 `vfr_jitter_threshold`，或 p95/p50 超过 `vfr_p95_p50_threshold`，或间隔不稳定但未触发上述阈值 | `review` |
| `TIMESTAMP_BROKEN` | 任一 delta 为 `0`（重复）或 `< 0`（倒序） | `reject` |

每个文件结论都附带：

- `verdict`：四类判定之一；
- `reason_codes`：稳定的机器可读原因码，例如 `DECLARED_FPS_MISMATCH`、`HIGH_JITTER`、`TIMESTAMP_REVERSAL`、`DUPLICATE_TIMESTAMP`、`NON_POSITIVE_DELTA`；
- `reason_text`：人类可读解释；
- `classification`：`ok` / `review` / `reject` / `error`。

### 无序根因归因

当主力视频轨重建出的 PTS 序列出现非正 delta（`TIMESTAMP_BROKEN`）时，工具会逐样本给出触发点归因，归因码追加在 `reason_codes` 之后，结构化明细写入 `timestamps.disorder_attribution`（单文件 `probe` 输出与批处理 JSON 报告一致），Markdown 报告的 review/reject 明细段落同步列出触发点。未出现非正 delta 的文件该区块为 `null`，原因码不受影响。

每个触发点记录样本索引（`sample_index`，即 PTS 未能前进的样本）、对应的 `stts_sample_delta`、当前及前一样本的 `ctts_composition_offset`、PTS 回退量以及归因码：

| 归因码 | 含义 |
| --- | --- |
| `STTS_ZERO_DELTA` | `stts` sample_delta 为 0，解码时间未前进，造成重复时间戳 |
| `CTTS_DUPLICATE_OFFSET` | 相邻样本 `ctts` 偏移相同且 `stts` 未前进，显示时间完全重复 |
| `CTTS_NEGATIVE_OFFSET_REVERSAL` | 触发样本的 `ctts` 负偏移较前帧下降，把显示时间拉到前帧之前 |
| `CTTS_COMBINATION_REVERSAL` | `stts` 为正、偏移均非负，但相邻 `ctts` 组合（大偏移接小偏移）仍造成跨帧回退 |

归因只针对主力视频轨的样本索引，音频等其他轨不参与；`ctts` 版本 0（无符号）与版本 1（有符号）都会展开后参与判定。触发点列表在报告中最多保留 100 条，超出时 `truncated` 置真，`trigger_count` 仍为完整计数。

`ok` / `review` / `reject` 的映射来自配置。相同输入、相同配置和相同排序规则产生相同结论；批处理按报告相对路径排序，不使用随机数或当前时间参与分类。

---

## 命令行与退出码

| 命令 | 作用 |
| --- | --- |
| `python -m fpsprobe probe <file>` | 单文件分析，JSON 输出到 stdout |
| `python -m fpsprobe batch --config <path>` | 扫描目录、逐文件分析、入库并写报告 |
| `python -m fpsprobe report --run-id <id> --format json\|md` | 从 SQLite 重新生成报告 |
| `python -m fpsprobe serve --port 5180` | 启动只读本地 HTTP 服务 |
| `python -m fpsprobe fixtures gen` | 生成 5 个确定性 fixture |
| `python -m fpsprobe selftest` | 生成 fixture 并跑端到端冒烟检查 |

`batch` 与 `report` 支持 `--db <path>` 覆盖默认台账路径。`probe` 与 `batch` 支持 `--primary-track first_video|longest_video|<index>`。

退出码约定：

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功；处理的文件中不存在 `reject` |
| `1` | 处理成功，但分类结果中存在至少一个 `reject` |
| `2` | 配置错误、输入错误或单文件无法读取/解析 |
| `3` | 内部错误 |

错误输出为 JSON，例如：

```json
{"error":{"code":"CONFIG_MISSING_FIELD","message":"missing required configuration field: thresholds.vfr_jitter_threshold"}}
```

---

## 台账（SQLite）

默认数据库：`workdir/fpsprobe.db`。四张表：

| 表 | 关键字段 | 作用 |
| --- | --- | --- |
| `runs` | `run_id`、`config_fingerprint`、`started_at`、`finished_at`、`summary_json` | 一次批处理的生命周期与汇总 |
| `files` | `path`、`sha256`、`size_bytes`、`config_fingerprint`、`status`、`error_code` | 文件事实台账；唯一键 `(path, sha256, config_fingerprint)` |
| `file_verdicts` | `run_id`、`file_id`、`verdict`、`classification`、`reason_codes`、`payload_json` | 每个 run 中每个文件的完整判定 |
| `timeline_samples` | `run_id`、`file_id`、`frame_count`、`stored_frame_count`、`truncated`、`summary_json` | 有界时间线明细；帧数超过 `max_detail_frames` 时只存摘要 |

幂等行为：

- 同一 `path + sha256 + config_fingerprint` 重复入库不会在 `files` 产生重复行；
- 同一 `run_id + file_id` 重复记录 verdict 会更新该行而不追加；
- `finish_run` 只完成一次；之后的 `upsert_file` / `record_verdict` 会被拒绝，重复调用 `finish_run` 是无副作用 no-op。
- 重复运行同一批文件会产生新的 `runs` 和新的 `file_verdicts`，但不会重复 `files`。

---

## 报告

批处理默认写出：

- `workdir/report.json`
- `workdir/report.md`

JSON 报告结构：

- `run_meta`：`run_id`、`config_fingerprint`、`started_at`、`finished_at`、`tool_version`；
- `summary`：文件总数、`ok/review/reject/error` 计数、各 verdict 计数、相对输入目录标签；
- `files`：每个文件的容器信息、时间戳统计、直方图、verdict、原因码、分档；
- `failures`：单文件失败记录（相对路径、错误码、消息）。

`fixtures/expected/sample_report.json` 是**结构契约样例**。测试只校验 key 集合与类型，不逐值硬比，避免机器或时间敏感。报告中不写绝对路径：文件路径相对输入目录（单输入目录时），源目录也使用相对标签。

Markdown 报告包含总览表、所有文件表，以及每个 `review` / `reject` 文件的明细段落和失败列表。

---

## 本地 HTTP API

服务使用标准库 `ThreadingHTTPServer`，默认只绑定 `127.0.0.1`；显式传入非回环地址会被拒绝（`NON_LOOPBACK_BIND`）。

| 接口 | 说明 |
| --- | --- |
| `GET /health` | 返回 `{"status":"ok"}` |
| `GET /api/runs` | 台账中的 run 列表 |
| `GET /api/runs/<run_id>` | 单个 run 的汇总与文件列表 |
| `GET /api/runs/<run_id>/files` | 文件判定列表，支持 `?verdict=review`（也接受 verdict 名） |
| `GET /api/report/<run_id>.json` | 基于台账重新生成 JSON 报告 |

所有接口只读，不接受上传，不把请求路径拼接到文件系统调用中。run id 经过严格白名单校验；含 `..`、反斜杠、空段、编码后的路径穿越片段会返回 `400`，未知 run 返回 `404`。服务不做任何报告写盘。

---

## 测试与离线约定

```bash
python -m pytest -q
```

- 全部测试离线运行，不访问网络；
- 不要求安装 ffmpeg / ffprobe，也不要求 ffmpeg 生成的素材；
- fixture 由 `fixtures/generator.py` 纯 Python 合成，固定创建时间字段为 0，重复运行 sha256 一致；
- 内置解析器只读 box 头与元数据表，不读取 `mdat` 载荷；
- 测试覆盖 32/64 位 box、未知 box、`size=0`、截断文件、四类判定、台账幂等、报告结构、API 路径穿越与 CLI 退出码；
- 当前测试套件没有因缺少 ffprobe 而跳过的分支。若后续新增 ffprobe 分支测试，应使用 `pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")`，并在本节注明。

`ffprobe` 适配器默认关闭：配置 `adapter: ffprobe` 且本机已安装 ffprobe 时才会调用；若未安装，流水线回退到内置解析器，不因缺失而失败。

---

## 已知限制

1. 目前只覆盖 MP4（ISO-BMFF）。不支持 MPEG-TS、WebM、MKV、AVI。
2. 不读取 `mdat` 样本数据，不解码像素；结论仅来自容器与时间表。
3. 声明帧率由 `mvhd` 影片时长与主力视频轨样本数推导；若容器没有可用影片时长，会给出 `DECLARED_FPS_MISSING`。
4. 仅分析逐帧 PTS 的一阶差分，不进行场景复杂度、光流或 AI 帧率推断。
5. 不修复输入文件、不转码、不抽帧。
6. 单机串行批处理；没有队列、分布式调度、登录、多租户或云同步。
7. 报告中的相对路径按单输入目录规则生成；多输入目录时使用相对项目目录的标签。
8. Web 前端不在范围内；HTTP 仅提供只读 JSON API。

---

## 素材源项目自检

以下数据来自当前仓库的最终自检：

- Python 源文件数（含测试与 fixture 生成器）：**41**
- Python 总行数（`find fpsprobe fixtures tests -name '*.py' | xargs wc -l`）：**4296**
- 最大单文件行数：**296**（`fpsprobe/store/ledger.py`）
- 全部单文件 ≤ 300 行
- `pytest -q`：**56 passed**
- `ruff check fpsprobe fixtures tests`（项目配置）：通过

fixture sha256（两次独立生成一致）：

| 文件 | 字节数 | sha256 |
| --- | ---: | --- |
| `cfr_30.mp4` | 631 | `d958b13a27217b917c8a0cfc3113150c83940bfcf90e7435445b64d7860b8f63` |
| `cfr_2997.mp4` | 631 | `5bbf631eca050c87fb043af8e9e59f9dffa97cf141303f8a48c1398aa1dd033b` |
| `vfr_jitter.mp4` | 1103 | `980b195cce11b36e5d8517b79dbc5511abac5fff6cb3be6a0dfb0460c48170b1` |
| `broken_order.mp4` | 703 | `75fabf2a07c54fb8eb43fc7461d683b5396ebace7d58a9489c7b36fe6a44ee20` |
| `moov_at_end.mp4` | 1086 | `bc1dfc87b9ba11938741506648ca4729603d8ef8683b579d05757862bf8fc329` |

夹具预期结果：

| 文件 | verdict | classification |
| --- | --- | --- |
| `cfr_30.mp4` | `CFR_OK` | `ok` |
| `cfr_2997.mp4` | `CFR_MISMATCH` | `review` |
| `vfr_jitter.mp4` | `VFR_SUSPECT` | `review` |
| `broken_order.mp4` | `TIMESTAMP_BROKEN` | `reject` |
| `moov_at_end.mp4` | `CFR_OK` | `ok` |

自检命令：

```bash
python -m pytest -q
python -m fpsprobe fixtures gen
python -m fpsprobe probe fixtures/out/vfr_jitter.mp4
python -m fpsprobe batch --config fixtures/config/default.yaml
python -m fpsprobe serve --port 5180
```
