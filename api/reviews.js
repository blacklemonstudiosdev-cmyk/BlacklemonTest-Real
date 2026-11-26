/*
 * BlackLemon Studios - reviews endpoint (Vercel serverless function).
 *
 * POST /api/reviews  { title, rating, product, review, nickname, website }
 *   - ALL ratings 1-5 are accepted and counted toward the running average.
 *   - The newest reviews are stored (newest first) and the list is trimmed to
 *     the latest MAX_REVIEWS; older reviews are deleted to save repo storage.
 *   - A running { count, sum } across ALL reviews ever posted is kept so the
 *     displayed average reflects everyone, not just the 5 shown.
 *   - Empty honeypot "website" or invalid fields -> binned (not saved).
 *
 * data/reviews.json shape:
 *   { "reviews": [ {title,rating,product,review,nickname,date}, ... up to 5 ],
 *     "stats":   { "count": <int>, "sum": <int> } }
 *
 * Required environment variables (set in the Vercel project settings):
 *   GITHUB_TOKEN  - a token with Contents: Read and Write on your site repo
 *   GITHUB_REPO   - "owner/repo" of your site repo
 *   GITHUB_BRANCH - branch to commit to (defaults to "main")
 *   REVIEWS_PATH  - file path (defaults to "data/reviews.json")
 */
'use strict';

var MAX_REVIEWS = 5;

function clean(s, max) {
  return String(s == null ? '' : s).replace(/[<>]/g, '').trim().slice(0, max);
}

function readBody(req) {
  if (req.body !== undefined && req.body !== null) {
    if (typeof req.body === 'string') { try { return Promise.resolve(JSON.parse(req.body)); } catch (e) { return Promise.resolve({}); } }
    return Promise.resolve(req.body);
  }
  return new Promise(function (resolve) {
    var data = '';
    req.on('data', function (c) { data += c; });
    req.on('end', function () { try { resolve(JSON.parse(data || '{}')); } catch (e) { resolve({}); } });
    req.on('error', function () { resolve({}); });
  });
}

function send(res, code, obj) {
  res.statusCode = code;
  res.setHeader('Content-Type', 'application/json');
  res.end(JSON.stringify(obj));
}

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') { return send(res, 405, { error: 'Method not allowed' }); }

  var body = await readBody(req) || {};

  // honeypot -> silently drop
  if (body.website) { return send(res, 200, { published: false }); }

  var rating = parseInt(body.rating, 10) || 0;
  var title = clean(body.title, 120);
  var nickname = clean(body.nickname, 60);
  var review = clean(body.review, 2000);
  var product = clean(body.product, 80);

  if (!title || !nickname || !review || rating < 1 || rating > 5) {
    return send(res, 400, { error: 'Missing or invalid fields' });
  }

  var TOKEN = process.env.GITHUB_TOKEN;
  var REPO = process.env.GITHUB_REPO;
  var BRANCH = process.env.GITHUB_BRANCH || 'main';
  var FILE = process.env.REVIEWS_PATH || 'data/reviews.json';
  if (!TOKEN || !REPO) { return send(res, 500, { error: 'Server not configured' }); }

  var api = 'https://api.github.com/repos/' + REPO + '/contents/' + FILE;
  var ghHeaders = {
    'Authorization': 'Bearer ' + TOKEN,
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'blacklemon-reviews',
    'X-GitHub-Api-Version': '2022-11-28'
  };
  var entry = { title: title, rating: rating, product: product, review: review, nickname: nickname, date: new Date().toISOString() };

  async function commitOnce() {
    var sha;
    var reviews = [];
    var stats = { count: 0, sum: 0 };
    var getRes = await fetch(api + '?ref=' + encodeURIComponent(BRANCH), { headers: ghHeaders });
    if (getRes.status === 200) {
      var cur = await getRes.json();
      sha = cur.sha;
      try {
        var decoded = Buffer.from(cur.content || '', 'base64').toString('utf-8');
        var parsed = JSON.parse(decoded);
        if (parsed && Array.isArray(parsed.reviews)) { reviews = parsed.reviews; }
        if (parsed && parsed.stats && typeof parsed.stats.count === 'number') {
          stats.count = parsed.stats.count; stats.sum = parsed.stats.sum || 0;
        } else {
          // migrate: no stats yet -> seed from whatever reviews are present
          stats.count = reviews.length;
          stats.sum = reviews.reduce(function (a, r) { return a + (parseInt(r.rating, 10) || 0); }, 0);
        }
      } catch (e) { reviews = []; stats = { count: 0, sum: 0 }; }
    } else if (getRes.status !== 404) {
      throw new Error('read failed: ' + getRes.status);
    }

    // count every review toward the running average
    stats.count += 1;
    stats.sum += rating;

    // keep only the newest MAX_REVIEWS on the site (oldest auto-deleted)
    reviews.unshift(entry);
    reviews = reviews.slice(0, MAX_REVIEWS);

    var payload = { reviews: reviews, stats: { count: stats.count, sum: stats.sum } };
    var content = Buffer.from(JSON.stringify(payload, null, 2) + '\n', 'utf-8').toString('base64');
    var putBody = { message: 'Add review', content: content, branch: BRANCH };
    if (sha) { putBody.sha = sha; }
    var putRes = await fetch(api, { method: 'PUT', headers: ghHeaders, body: JSON.stringify(putBody) });
    if (putRes.status === 200 || putRes.status === 201) { return payload; }
    if (putRes.status === 409) { var c = new Error('conflict'); c.conflict = true; throw c; }
    var txt = await putRes.text();
    throw new Error('write failed: ' + putRes.status + ' ' + txt);
  }

  try {
    var payload;
    try { payload = await commitOnce(); }
    catch (e) { if (e && e.conflict) { payload = await commitOnce(); } else { throw e; } }
    var avg = payload.stats.count ? (payload.stats.sum / payload.stats.count) : 0;
    return send(res, 200, { published: true, review: entry, reviews: payload.reviews, count: payload.stats.count, average: avg });
  } catch (err) {
    console.error('review commit error:', err && err.message ? err.message : err);
    return send(res, 502, { error: 'Could not save review' });
  }
};
