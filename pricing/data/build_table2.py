import csv

# Load model ID map
id_map = {}
with open('model-id-map.csv') as f:
    for row in csv.DictReader(f):
        id_map[row['openrouter_id']] = row['aa_slug']

# Load all scores
scores = {}
with open('scores-artificialanalysis.csv') as f:
    for row in csv.DictReader(f):
        slug = row['source_model_name']
        bench = row['benchmark']
        score = row['score']
        if slug not in scores:
            scores[slug] = {}
        scores[slug][bench] = score

# Load price-vs-quality
models = []
with open('price-vs-quality.csv') as f:
    for row in csv.DictReader(f):
        oid = row['openrouter_id']
        iq = row.get('aa_intelligence_index', '').strip()
        if iq and iq.replace('.','',1).replace('-','',1).isdigit() and float(iq) >= 35:
            models.append({
                'oid': oid,
                'name': row['name'],
                'iq': float(iq),
                'prompt': row['prompt_usd_per_mtok'],
                'completion': row['completion_usd_per_mtok'],
                'blended': row['blended_usd_per_mtok'],
            })

models.sort(key=lambda m: m['iq'], reverse=True)

def fmt(val, width, decimal=3, dollar=False):
    if not val or val == '-':
        return '-'.rjust(width)
    v = float(val)
    if dollar:
        if v == int(v):
            return f"{int(v)}".rjust(width)
        else:
            return f"{v:.{decimal}f}".rstrip('0').rstrip('.').rjust(width)
    return f"{v:.{decimal}f}".rjust(width)

h = "{:<48} {:>6} {:>10} {:>10} {:>8} {:>8} {:>7} {:>7} {:>8} {:>9}"
r = "{:<48} {:>6.1f} {:>10} {:>10} {:>8} {:>8} {:>7} {:>7} {:>8} {:>9}"

print(h.format("Model", "IQ", "CodingIdx", "LiveCodeB", "SciCode", "TB Hard", "Tau2", "In$/M", "Out$/M", "Blend$/M"))
print("-" * 125)

for m in models:
    slug = id_map.get(m['oid'], '')
    sd = scores.get(slug, {})
    
    coding_idx = fmt(sd.get('artificial_analysis_coding_index', '-'), 10, 1)
    livecode = fmt(sd.get('livecodebench', '-'), 10)
    scicode  = fmt(sd.get('scicode', '-'), 8)
    tb_hard  = fmt(sd.get('terminalbench_hard', '-'), 8)
    tau2     = fmt(sd.get('tau2', '-'), 7)
    
    inp = fmt(m['prompt'], 7, dollar=True)
    out = fmt(m['completion'], 8, dollar=True)
    bl  = fmt(m['blended'], 9, dollar=True)
    
    # Try broader name matching if slug didn't find scores
    if not sd:
        n_clean = m['name'].lower().replace(':','').replace('-','').replace(' ','').replace('.','')
        for sslug, sdata in scores.items():
            sn = sslug.lower().replace('-','').replace('_','').replace('.','')
            if sn in n_clean or n_clean in sn:
                if coding_idx.strip() == '-' and 'artificial_analysis_coding_index' in sdata:
                    coding_idx = fmt(sdata['artificial_analysis_coding_index'], 10, 1)
                if livecode.strip() == '-' and 'livecodebench' in sdata:
                    livecode = fmt(sdata['livecodebench'], 10)
                if scicode.strip() == '-' and 'scicode' in sdata:
                    scicode = fmt(sdata['scicode'], 8)
                if tb_hard.strip() == '-' and 'terminalbench_hard' in sdata:
                    tb_hard = fmt(sdata['terminalbench_hard'], 8)
                if tau2.strip() == '-' and 'tau2' in sdata:
                    tau2 = fmt(sdata['tau2'], 7)

    print(r.format(m['name'], m['iq'], coding_idx, livecode, scicode, tb_hard, tau2, inp, out, bl))
