# Keep keyboard focus through original-file reads

Activating the original reader with Tab/Enter disabled the focused button while
waiting for a response. Chrome then moved focus to the document body. Enabling
the button after the read did not restore it, so a subsequent Space could not
continue to the next part.

Busy controls now use `aria-disabled` while the existing busy guard rejects
repeat activation. They keep their current native focus state and show a busy
appearance. On a valid EOF response, focus moves from Next to the visible Reset
control only if Next is still focused; Next then becomes natively disabled.
Another control selected during the request retains focus. Closed or stale
readers cannot move focus or commit a reply. Source binding, hashes, byte-window
limits, cancellation checks and callback contracts are unchanged.

Validation includes286 related reader/detail tests with no skips, full
TypeScript checking and an isolated full frontend build. Nine new cases cover
pending Next/Reset, repeated activation, non-EOF/EOF, retry, moved focus and lost
selection ownership. Existing test bodies are preserved apart from the busy
control assertion that reflects the new accessible state.

An isolated Chrome for Testing component uses the actual reader module and
generated UTF-8 data. Its unchanged baseline reproduces focus loss after a real
Tab/Enter read. The candidate keeps focus, rejects repeated Enter during the
pending read, supports Space to continue, transfers focus at EOF, preserves
Reset focus, and leaves focus moved elsewhere untouched. It completed five
generated reads with no browser console warnings or errors. A controlled1000ms
fixture response supplies an observable pending state; it is not a product
latency measurement. Chrome also exposes the scrollable text area in native
Tab order; that accessible navigation remains intact.

These source and component checks do not establish actual Alden/native input,
installed delivery, representative semantic/answer quality or release. Retained
original-data checks are recorded separately. Alden remains off and the existing
installation, model/service ownership and formal release holds remain.
