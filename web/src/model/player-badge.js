/*
 * THE ENHANCE BADGE IN THE VIDEO OSD (pure).
 *
 * What the chip says is decided here from the SESSION RECORD the server sent and from
 * live-apply.js activeState(), never from what the panel selected (AGENTS.md invariant 7). The
 * wording is the server's own (record.Summary, which already carries its honest negatives such as
 * "Enhancement not applied (video is being streamed as-is)") or the client's existing
 * activeState() label. Nothing is invented here.
 *
 *   caps   the probe answer; the chip exists only while the plugin's server side answered fully
 *   s      state.lastServerState (or null)
 *   act    { key: 'waiting'|'unavailable'|'off'|'active', label, why }  from activeState()
 *
 *   link   linkProfile(): { noDirectPlay, enhanceOff, kbps } (optional). It is the CLIENT's own
 *          decision, so it is worded as the client's (`note`), apart from the server's text.
 *
 * Returns { show, key, text, title, note }. text is the full wording: the chip clips it with CSS
 * and puts the same words in its tooltip, so nothing is lost to the clipping. note is the slow-link
 * reason ('' when the link changes nothing) and does not depend on `show`.
 */
import { linkNote } from './link-profile.js';

// The server says this when a record exists but no pass ran and there is nothing to add.
var GENERIC = 'No enhancement';

export function badgeModel(caps, s, act, link) {
    var note = linkNote(link);
    if (!caps || !caps.full || !act) {
        return { show: false, key: 'hidden', text: '', title: '', note: note };
    }

    var summary = s && typeof s.Summary === 'string' ? s.Summary.trim() : '';
    var text;
    var title = act.why || act.label;

    if (act.key === 'active') {
        text = summary || act.label;
        title = summary || title;
    } else if (act.key === 'off' && s && s.Known && summary && summary !== GENERIC) {
        // The server named the reason (stream copy, burn-in, concurrency cap, viewer chose Off).
        text = summary;
        title = summary + (s.Status ? ' (' + s.Status + ')' : '');
    } else {
        text = act.label;
    }

    return { show: true, key: act.key, text: text, title: title, note: note };
}
