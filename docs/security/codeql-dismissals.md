# CodeQL 告警处置记录

本文件记录 CodeQL 报出、经人工核实后判定为**误报**并 dismiss 的告警，
以及判定依据。每条都要写清三件事：告警是什么、为什么在这里不成立、
真实防线由谁提供（含可复跑的验证命令）。

新建或修改告警处置时请同步本文件；只在 GitHub 上点 dismiss 而不留记录，
下一个人会重新怀疑同一段代码。

---

## py/path-injection：导入根白名单的误报族

**告警位置**（master `03de54f` 复扫，8 条结果中 5 条 open）

| 告警 | 位置 | 该行在做什么 |
| --- | --- | --- |
| #7 | `src/jmlib/governance.py:316` | `if not src.is_file():` |
| #8 | `src/jmlib/governance.py:430` | `os.rename(src, dst)` |
| #9 | `src/jmlib/governance.py:451` | `if not dst.is_file() or src.exists():` |
| #10 | `src/jmlib/import_scan.py:169` | `for p in directory.rglob("*"):`（`_dir_bytes`） |
| #22 | `src/jmlib/import_scan.py:387` | `if not requested.is_dir():`（`scan` 入口） |

历史条目：PR #4 分析结果里的 `#19`/`#20`/`#21` 是同族告警的旧行号，已
dismiss；PR #5 修掉了真正的两条（`scan()` 曾完全不校验白名单、
`plan_single()` 曾在校验前 resolve），修完后其余行号整体下移，本条族重新
以 `#7`–`#22` 的编号出现。

**为什么是误报**

`py/path-injection` 用两态污点模型判断「用户输入是否可能被拼进文件路径」，
它把 `Path.resolve()`、`Path.is_dir()`、`Path.is_file()`、`Path.exists()`、
`Path.rglob()`、`os.rename()` 都当作访问 sink，只把 `str.startswith` 认作
`SafeAccessCheck`。这五处的实际执行顺序是：

1. `paths.normalize_untrusted()` 把用户文本转成绝对路径 —— 纯字符串运算
   （`expanduser` + `normpath` + `abspath`），不 `stat`、不解析符号链接；
2. 在**归一化结果**上用 `str.startswith(base + os.sep)` 逐条比对白名单根；
3. 只有第 2 步通过的值才会走到 `resolve()` / `is_dir()`；
4. `resolve()` 之后再比一次白名单（拦住「根内的符号链接指向根外」）。

也就是说，sink 之前必定已有白名单判定，且判定发生在任何文件系统访问之前。
CodeQL 之所以仍然报，是因为白名单是**一组根**（`JMLIB_IMPORT_ROOTS` +
项目内固定根），判定写成循环，而模型只识别「单个常量前缀」的
`str.startswith` 守卫；多根循环不在它的识别范围内。这与实际安全性无关。

**真实防线**

- `src/jmlib/paths.py` 的 `allowed_import_roots()` / `contained_in()` /
  `resolve_inside_roots()` 是白名单的唯一拥有者；
- 库层 `import_apply._single_item()` 与 `import_scan.scan()` 也各自独立检查
  一次，不依赖 HTTP 层（PR #5 补上的，此前 `scan()` 确实只信调用方）；
- `governance._join_under()` 用同一条规则产出 `_unresolved_source()` /
  `_archive_target()`，`promote_plan` / `promote_apply` / `promote_rollback`
  的每一次 `is_file` / `os.rename` / `exists` 都只用这两个函数的返回值；
- 越界一律拒绝，且拒绝发生在任何文件系统调用之前。

**验证**

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
$env:PYTHONPATH = "src"
& $py .tmp\run_a_suite.py     # 末行 SUITE_OK
& $py -m pytest -q tests      # 14 passed
& $py -m unittest discover -s tests -t .   # Ran 14 tests ... OK
```

其中 B1 批次覆盖四种拒绝形态：越界 403、`../` 穿越 403、空路径 400、
白名单内不存在的路径 → 200 + `ok=False` + `error` 含 `not a directory`；
`tests/test_paths_containment.py` 另外把「同名前缀兄弟目录必须拒绝」这一
容易写错的情形钉进 CI。

**结论**：该族告警均为误报，dismiss reason = `won't fix`，说明见上。
