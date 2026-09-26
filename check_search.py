import httpx

r = httpx.get('http://localhost:8000/api/companies/11111111-1111-1111-1111-111111111111/scraped-data')
posts = r.json()
query = 'orchan consulting asia'.lower()
matches = []
for p in posts:
    content = (p.get('comment_text') or '').lower()
    kw = (p.get('keyword_string') or '').lower()
    author = 'user'
    if query in content or query in kw or query in author:
        matches.append(p)
print(f"Total posts: {len(posts)}")
print(f"Matches for '{query}': {len(matches)}")
for m in matches[:5]:
    print('Match keyword:', m.get('keyword_string'), '| platform:', m.get('platform'))
