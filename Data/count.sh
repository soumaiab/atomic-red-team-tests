cd "c:/Users/soums/OneDrive/Desktop/Research/atomic-red-team-tests/Data" && ls -la page-*.json 2>&1
echo "---"
python -c "
import json, glob
files = sorted(glob.glob('page-*.json'), key=lambda x: int(x.split('-')[1].split('.')[0]))
for f in files:
    with open(f, encoding='utf-8') as fh:
        d = json.load(fh)
    hits = d['hits']['hits']
    total = d['hits']['total']
    print(f'{f}: hits.total={total}, hits.hits count={len(hits)}')
print('sum of hits.hits across files:', sum(len(json.load(open(f, encoding=\"utf-8\"))['hits']['hits']) for f in files))
"
