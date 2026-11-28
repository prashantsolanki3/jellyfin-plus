// Run: node web/src/model/link-profile.test.mjs   (no framework; exits non-zero on failure)
import assert from 'node:assert/strict';
import {
    classify, linkPref, linkNote, readLinkPref, writeLinkPref, slowBody, slowUrl,
    MIN_KBPS, MAX_KBPS, ENHANCE_MIN_MBPS, PREF_KEY
} from './link-profile.js';
import { badgeModel } from './player-badge.js';
import { CONNECTION } from './controls-data.js';

var FULL = { full: true };
var ACTIVE = { key: 'active', label: 'Enhancing now', why: 'w' };
var OFF = { key: 'off', label: 'Enhancement off', why: 'w' };
var WAIT = { key: 'waiting', label: 'Waiting for the server', why: 'w' };

var passed = 0;
function t(name, fn) {
    try { fn(); passed++; console.log('ok   ' + name); } catch (e) { console.log('FAIL ' + name + '\n' + e.message); process.exitCode = 1; }
}

t('pref parsing', function () {
    assert.equal(linkPref(null), 'auto');
    assert.equal(linkPref(' Slow '), 'slow');
    assert.equal(linkPref('remote'), 'slow');
    assert.equal(linkPref('off'), 'fast');
    assert.equal(linkPref('lan'), 'fast');
    assert.equal(linkPref('junk'), 'auto');
});

t('classify: measured remote link (272 ms, 2.6 Mbps): no direct play, axes off, cap 60% of throughput', function () {
    assert.deepEqual(classify(272, 2.6, null, false), { noDirectPlay: true, enhanceOff: true, kbps: 1560 });
});

t('classify: LAN is fast', function () {
    var c = classify(3, 400, null, false);
    assert.equal(c.noDirectPlay, false);
    assert.equal(c.enhanceOff, false);
});

t('classify: either signal alone disables direct play', function () {
    assert.equal(classify(200, 500, null, false).noDirectPlay, true);
    assert.equal(classify(5, 4, null, false).noDirectPlay, true);
});

t('classify (a): long RTT with plenty of throughput (272 ms, 50 Mbps) disables direct play but not enhancement', function () {
    var c = classify(272, 50, null, false);
    assert.equal(c.noDirectPlay, true);
    assert.equal(c.enhanceOff, false);
    assert.equal(c.kbps, MAX_KBPS);
});

t('classify (b): under ENHANCE_MIN_MBPS with no choice turns the axes off, and the badge says why', function () {
    var c = classify(40, 2, null, false);
    assert.equal(c.enhanceOff, true);
    var m = badgeModel(FULL, { Known: true, Summary: 'No enhancement' }, OFF, c);
    assert.ok(m.note.indexOf('Link slow (under 4 Mbps): enhancement off (tap Enhance to override)') >= 0, m.note);
});

t('classify: the 4 Mbps bound is exact', function () {
    assert.equal(ENHANCE_MIN_MBPS, 4);
    assert.equal(classify(10, 3.99, null, false).enhanceOff, true);
    assert.equal(classify(10, 4, null, false).enhanceOff, false);
    assert.equal(classify(10, 5, null, false).enhanceOff, false);
    assert.equal(classify(10, 5, null, false).noDirectPlay, true);
});

t('classify (c): an explicit choice is sent even at 2 Mbps, and the badge names the cap', function () {
    var c = classify(272, 2, null, true);
    assert.equal(c.enhanceOff, false);
    assert.equal(c.noDirectPlay, true);
    var m = badgeModel(FULL, { Known: true, Summary: 'Upscaled 1280x720 to 1920x1080 (b)' }, ACTIVE, c);
    assert.equal(m.note, 'Link slow: bitrate capped at 1.2 Mbps');
});

t('classify (d): unknown throughput with a long RTT leaves enhancement on', function () {
    var c = classify(272, null, null, false);
    assert.equal(c.enhanceOff, false);
    assert.equal(c.noDirectPlay, true);
    assert.equal(classify(272, undefined, null, false).enhanceOff, false);
});

t('classify: prefs override the measurement', function () {
    var f = classify(272, 2.6, 'fast', false);
    assert.equal(f.noDirectPlay, false);
    assert.equal(f.enhanceOff, false);
    var s = classify(3, 400, 'slow', false);
    assert.equal(s.noDirectPlay, true);
    assert.equal(s.enhanceOff, true);
    assert.equal(classify(3, 400, 'slow', true).enhanceOff, false);
});

t('linkNote: nothing for a link that changes nothing, one line per decision otherwise', function () {
    assert.equal(linkNote(classify(3, 400, null, false)), '');
    assert.equal(linkNote(null), '');
    assert.equal(linkNote(classify(272, 2, null, false)),
        'Link slow: bitrate capped at 1.2 Mbps. Link slow (under 4 Mbps): enhancement off (tap Enhance to override)');
});

t('badgeModel: no link argument means no note (existing callers)', function () {
    assert.equal(badgeModel(FULL, null, WAIT).note, '');
});

t('Connection (e): the control writes and reads the key, and fast means never slow', function () {
    var store = {};
    var storage = {
        getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
        setItem: function (k, v) { store[k] = String(v); }
    };
    assert.equal(CONNECTION.label, 'Connection');
    assert.deepEqual(CONNECTION.options.map(function (o) { return o.id; }), ['auto', 'fast', 'slow']);
    assert.equal(PREF_KEY, 'jellyfinPlus.linkProfile');
    assert.equal(readLinkPref(storage), 'auto');

    writeLinkPref(storage, 'fast');
    assert.equal(store['jellyfinPlus.linkProfile'], 'fast');
    assert.equal(readLinkPref(storage), 'fast');
    var c = classify(272, 0.6, readLinkPref(storage), false);
    assert.equal(c.noDirectPlay, false);
    assert.equal(c.enhanceOff, false);

    writeLinkPref(storage, 'slow');
    assert.equal(store['jellyfinPlus.linkProfile'], 'slow');
    assert.equal(readLinkPref(storage), 'slow');
    assert.equal(classify(3, 400, readLinkPref(storage), false).noDirectPlay, true);

    writeLinkPref(storage, 'auto');
    assert.equal(store['jellyfinPlus.linkProfile'], '');
    assert.equal(readLinkPref(storage), 'auto');
});

t('Connection: a blocked store reads as auto and writes fail soft', function () {
    var blocked = { getItem: function () { throw new Error('denied'); }, setItem: function () { throw new Error('denied'); } };
    assert.equal(readLinkPref(blocked), 'auto');
    assert.equal(writeLinkPref(blocked, 'fast'), false);
});

t('classify: cap clamped, default when throughput unknown', function () {
    assert.equal(classify(300, 0.5, null, false).kbps, MIN_KBPS);
    assert.equal(classify(300, 9.9, null, false).kbps, MAX_KBPS);
    assert.equal(classify(300, null, null, false).kbps, MIN_KBPS * 2);
});

t('slowBody: no direct play, cap lowered but never raised', function () {
    var out = JSON.parse(slowBody(JSON.stringify({ MaxStreamingBitrate: 120000000, EnableDirectStream: true }), 1500));
    assert.equal(out.EnableDirectPlay, false);
    assert.equal(out.EnableDirectStream, true);
    assert.equal(out.MaxStreamingBitrate, 1500000);
    assert.equal(slowBody(JSON.stringify({ MaxStreamingBitrate: 1000000, EnableDirectPlay: false }), 1500), null);
    assert.equal(slowBody('not json', 1500), null);
    assert.equal(slowBody('', 1500), null);
});

t('slowUrl: query form', function () {
    assert.equal(slowUrl('/Items/x/PlaybackInfo?UserId=u', 1500),
        '/Items/x/PlaybackInfo?UserId=u&enableDirectPlay=false&maxStreamingBitrate=1500000');
    assert.equal(slowUrl('/Items/x/PlaybackInfo?MaxStreamingBitrate=900000&EnableDirectPlay=true', 1500),
        '/Items/x/PlaybackInfo?MaxStreamingBitrate=900000&EnableDirectPlay=false');
    assert.equal(slowUrl('/p?maxStreamingBitrate=8000000', 1500), '/p?maxStreamingBitrate=1500000&enableDirectPlay=false');
});

console.log(passed + ' passed');
