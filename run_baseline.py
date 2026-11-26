"""Baseline 실행: python run_baseline.py [seed ...]"""
import sys

from config import Config
from system import run
from analysis import analyze, check_model


def fmt(s):
    return "n=%-4d 평균 %6.1f  P50 %6.1f  P90 %6.1f  P95 %6.1f" % (s["n"], s["mean"], s["p50"], s["p90"], s["p95"])


def report(cfg, events, show_checks=True):
    a = analyze(events, cfg)
    if show_checks:
        print("== 모델 검증 ==")
        for name, ok, detail in check_model(events, cfg):
            print(("  [통과] " if ok else "  [실패] ") + name + ("  - " + detail if detail else ""))
    print("== 시간 지표 (분) / 집계 구간 %d~%d ==" % (cfg.evaluation.warmup_end, cfg.evaluation.measure_end))
    for g, r in a["timing"].items():
        print("[%s] 접수 %d, 촬영완료 %d, 미완료(접수) %d, 미완료(촬영후) %d | 24h내 %.1f%%  익일까지 %.1f%%" % (
            g, r["요청 접수"], r["촬영 완료"], r["미완료(접수 기준)"], r["미완료(촬영 완료 기준)"],
            100 * r["경과 24h 이내 완료율"], 100 * r["촬영일 익일까지 완료율"]))
        for k in ("전체 소요(요청→최종보고)", "촬영 후 최종보고", "안전확인 대기", "촬영 대기", "최종판독 대기",
                  "응급 초기보고(가판독-촬영)"):
            if r[k]["n"]:
                print("    %-24s %s" % (k, fmt(r[k])))
    print("== 자원 ==")
    for k, v in a["resource"].items():
        print("  %-34s %s" % (k, ("%.1f%%" % (100 * v)) if isinstance(v, float) else v))
    print("== 대기열 (집계 구간) ==")
    for q, v in sorted(a["queue"].items()):
        print("  %-18s 시간평균 %.2f  최대 %d" % (q, v["시간평균"], v["최대"]))
    return a


if __name__ == "__main__":
    seeds = [int(x) for x in sys.argv[1:]] or [Config().evaluation.seed]
    for seed in seeds:
        cfg = Config()
        cfg.evaluation.seed = seed
        print("########## seed %d ##########" % seed)
        report(cfg, run(cfg))
