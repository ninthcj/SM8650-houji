targets = [
    "pineapple",
]

la_variants = [
    "consolidate",
    "gki",
    "perf",
]

def get_all_variants():
    return [(t, v) for t in targets for v in la_variants]
