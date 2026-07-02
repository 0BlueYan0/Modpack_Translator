from modpack_translator.gui.stats import build_stats_text


def test_window_speed_when_samples_dense():
    # 視窗內 2 筆樣本、10 秒完成 20 對 → 視窗速度 2.0 句/秒(本地模式既有行為)
    samples = [(100.0, 0), (110.0, 20)]
    text = build_stats_text(
        now=110.0, start_time=100.0, samples=samples,
        pairs_done=20, total_pairs=220,
    )
    # 剩餘 200 對 / 2.0 = 100 秒
    assert text == "速度：2.0 句/秒  |  已用時間：00:00:10  |  預計剩餘：00:01:40"


def test_average_fallback_when_window_sparse():
    # 遠端慢速:視窗內只有 1 筆樣本 → 退回累計平均 4/128 = 0.03125 句/秒
    # (數值刻意選二進位可精確表示,避免浮點誤差影響斷言)
    samples = [(120.0, 4)]
    text = build_stats_text(
        now=128.0, start_time=0.0, samples=samples,
        pairs_done=4, total_pairs=100,
    )
    # 剩餘 96 對 / 0.03125 = 3072 秒 = 00:51:12
    assert text == "速度：0.03 句/秒（平均）  |  已用時間：00:02:08  |  預計剩餘：00:51:12"


def test_stalled_window_falls_back_to_average():
    # 視窗內 2 筆樣本但完成數沒有前進(單條長推理)→ 不顯示停滯字樣,退回平均
    samples = [(150.0, 5), (155.0, 5)]
    text = build_stats_text(
        now=160.0, start_time=0.0, samples=samples,
        pairs_done=5, total_pairs=10,
    )
    # 平均 5/160 = 0.03125 句/秒;剩餘 5 對 → 160 秒
    assert text == "速度：0.03 句/秒（平均）  |  已用時間：00:02:40  |  預計剩餘：00:02:40"


def test_before_first_pair_shows_translating():
    # 一條都還沒完成(連線中或第一條推理中)→ 顯示翻譯中/計算中
    text = build_stats_text(
        now=20.0, start_time=0.0, samples=[],
        pairs_done=0, total_pairs=50,
    )
    assert text == "速度：翻譯中…  |  已用時間：00:00:20  |  預計剩餘：計算中…"


def test_total_pairs_clamped_to_done_plus_one():
    # 掃描估算偏低時 total 以 done+1 夾住,剩餘至少 1 對
    samples = [(0.0, 0), (10.0, 10)]
    text = build_stats_text(
        now=10.0, start_time=0.0, samples=samples,
        pairs_done=10, total_pairs=8,
    )
    assert text == "速度：1.0 句/秒  |  已用時間：00:00:10  |  預計剩餘：00:00:01"
