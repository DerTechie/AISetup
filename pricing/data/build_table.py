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

# Load price-vs-quality for model names & IQs
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
            })

models.sort(key=lambda m: m['iq'], reverse=True)

def fmt(val, width, decimal=3):
    if not val or val == '-':
        return '-'.rjust(width)
    return f"{float(val):.{decimal}f}".rjust(width)

header_f = "{:<48} {:>6} {:>10} {:>10} {:>8} {:>8}"
row_f = "{:<48} {:>6.1f} {:>10} {:>10} {:>8} {:>8}"

print(header_f.format("Model", "IQ", "CodingIdx", "LiveCodeB", "SciCode", "TB Hard"))
print("-" * 95)

for m in models:
    slug = id_map.get(m['oid'], '')
    sd = scores.get(slug, {})
    
    coding_idx = fmt(sd.get('artificial_analysis_coding_index', '-'), 10, 1)
    livecode = fmt(sd.get('livecodebench', '-'), 10)
    scicode  = fmt(sd.get('scicode', '-'), 8)
    tb_hard  = fmt(sd.get('terminalbench_hard', '-'), 8)
    
    # If slug didn't match, try name-based lookup
    if not sd:
        for sslug, sdata in scores.items():
            n = m['name'].lower().replace(':','').replace('-','').replace(' ','')
            sn = sslug.lower().replace('-','').replace('_','').replace('.','')
            if sn in n or n in sn:
                if coding_idx.strip() == '-' and 'artificial_analysis_coding_index' in sdata:
                    coding_idx = fmt(sdata['artificial_analysis_coding_index'], 10, 1)
                if livecode.strip() == '-' and 'livecodebench' in sdata:
                    livecode = fmt(sdata['livecodebench'], 10)
                if scicode.strip() == '-' and 'scicode' in sdata:
                    scicode = fmt(sdata['scicode'], 8)
                if tb_hard.strip() == '-' and 'terminalbench_hard' in sdata:
                    tb_hard = fmt(sdata['terminalbench_hard'], 8)
    
    print(row_f.format(m['name'], m['iq'], coding_idx, livecode, scicode, tb_hard))
