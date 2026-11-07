/**
 * Linear agent — dashboard plugin
 *
 * One job: choose which Hermes agent answers Linear.
 *
 * Plain IIFE, no build step: React comes from the dashboard SDK and every node is
 * React.createElement. No bundler, no npm, no bundled React — that is the point.
 *
 * Backend: /api/plugins/linear-agent/{state,agent} (see plugin_api.py).
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__ || typeof window.__HERMES_PLUGINS__.register !== "function") {
    return;
  }

  var React = SDK.React;
  if (!React) return;
  var h = React.createElement;

  var hooks = SDK.hooks || {};
  var useState = hooks.useState || React.useState;
  var useEffect = hooks.useEffect || React.useEffect;
  var useCallback = hooks.useCallback || React.useCallback;
  var useMemo = hooks.useMemo || React.useMemo;

  var C = SDK.components || {};

  // --- graceful degradation: an older host may not expose every primitive ---
  function el(tag, className) {
    return function Fallback(props) {
      var rest = Object.assign({}, props);
      delete rest.children;
      return h(tag, Object.assign({ className: className }, rest), props && props.children);
    };
  }
  var Card = C.Card || el("div", "la-card");
  var CardHeader = C.CardHeader || el("div", "la-card-header");
  var CardTitle = C.CardTitle || el("h3", "la-card-title");
  var CardContent = C.CardContent || el("div", "la-card-content");
  var Badge = C.Badge || el("span", "la-badge");
  var Button = C.Button || el("button", "la-button");
  var Input = C.Input || el("input", "la-input");

  var API = "/api/plugins/linear-agent";

  function initials(name) {
    if (!name) return "?";
    var parts = String(name).replace(/[^a-zA-Z0-9 _-]/g, " ").trim().split(/[\s_-]+/);
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }

  function activeLabel(active) {
    return active || "the gateway default agent";
  }

  function StatusPill(props) {
    var bridge = props.bridge || {};
    if (bridge.reachable) {
      var text = "bridge reachable" + (bridge.version ? " · v" + bridge.version : "");
      return h(
        "span",
        { className: "la-pill la-pill-ok", title: bridge.url || "" },
        h("span", { className: "la-dot la-dot-ok" }),
        text
      );
    }
    var err = bridge.error || "bridge unreachable";
    return h(
      "span",
      { className: "la-pill la-pill-warn", title: err },
      h("span", { className: "la-dot la-dot-warn" }),
      "bridge unreachable"
    );
  }

  function ProfileCard(props) {
    var name = props.name;
    var selected = props.selected;
    var disabled = props.disabled;
    return h(
      "button",
      {
        type: "button",
        className: "la-profile" + (selected ? " la-profile-selected" : ""),
        "aria-pressed": selected ? "true" : "false",
        disabled: disabled,
        onClick: function () {
          props.onSelect(name);
        },
      },
      h("span", { className: "la-avatar" }, initials(name)),
      h(
        "span",
        { className: "la-profile-text" },
        h("span", { className: "la-profile-name" }, name || "default"),
        h("span", { className: "la-profile-sub" }, name ? "profile" : "gateway default")
      ),
      selected ? h("span", { className: "la-check", "aria-hidden": "true" }, "✓") : null
    );
  }

  function ErrorCard(props) {
    return h(
      Card,
      { className: "la-card la-card-error" },
      h(
        CardContent,
        null,
        h("p", { className: "la-error-title" }, "Could not load the Linear agent settings"),
        h("p", { className: "la-error-body" }, props.message),
        h(
          Button,
          { type: "button", className: "la-button", onClick: props.onRetry },
          "Retry"
        )
      )
    );
  }

  function AgentPage() {
    var state = useState({ loading: true, error: "", data: null });
    var current = state[0];
    var set = state[1];

    var pick = useState("");
    var picked = pick[0];
    var setPicked = pick[1];

    var saving = useState(false);
    var isSaving = saving[0];
    var setSaving = saving[1];

    var toast = useState("");
    var message = toast[0];
    var setMessage = toast[1];

    var filter = useState("");
    var query = filter[0];
    var setQuery = filter[1];

    var load = useCallback(function (isRetry) {
      set(function (prev) {
        return { loading: true, error: "", data: isRetry ? prev.data : null };
      });
      fetch(API + "/state", { headers: { Accept: "application/json" } })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          set({ loading: false, error: "", data: data });
          setPicked(data.active || "");
        })
        .catch(function (err) {
          set({ loading: false, error: String((err && err.message) || err), data: null });
        });
    }, []);

    useEffect(function () {
      load(false);
    }, [load]);

    var data = current.data || {};
    var profiles = data.profiles || [];
    var active = data.active || "";

    var visible = useMemo(
      function () {
        var q = (query || "").trim().toLowerCase();
        if (!q) return profiles;
        return profiles.filter(function (p) {
          return String(p).toLowerCase().indexOf(q) !== -1;
        });
      },
      [profiles, query]
    );

    function save() {
      setSaving(true);
      setMessage("");
      fetch(API + "/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ profile: picked }),
      })
        .then(function (r) {
          return r.json().then(function (body) {
            if (!r.ok) {
              var detail = (body && (body.detail || body.message)) || "HTTP " + r.status;
              throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
            }
            return body;
          });
        })
        .then(function (next) {
          set({ loading: false, error: "", data: next });
          setPicked(next.active || "");
          setSaving(false);
          setMessage("Saved. Takes effect on the next Linear issue — no restart needed.");
        })
        .catch(function (err) {
          setSaving(false);
          setMessage("Could not save: " + String((err && err.message) || err));
        });
    }

    var dirty = (picked || "") !== (active || "");

    if (current.loading && !current.data) {
      return h(
        "div",
        { className: "la-root" },
        h("div", { className: "la-head" }, h("h1", { className: "la-title" }, "Linear agent")),
        h("p", { className: "la-muted" }, "Loading…")
      );
    }

    if (current.error) {
      return h(
        "div",
        { className: "la-root" },
        h("div", { className: "la-head" }, h("h1", { className: "la-title" }, "Linear agent")),
        h(ErrorCard, {
          message: current.error,
          onRetry: function () {
            load(true);
          },
        })
      );
    }

    return h(
      "div",
      { className: "la-root" },
      h(
        "div",
        { className: "la-head" },
        h("div", null, h("h1", { className: "la-title" }, "Linear agent")),
        h(StatusPill, { bridge: data.bridge })
      ),
      h(
        "p",
        { className: "la-lede" },
        "Issues are answered by ",
        h("strong", { className: "la-accent" }, activeLabel(active)),
        "."
      ),
      data.env_override
        ? h(
            "p",
            { className: "la-note la-note-warn" },
            "LINEAR_AGENT_PROFILE is set in the environment and takes precedence over this page."
          )
        : null,
      profiles.length === 0
        ? h(
            Card,
            { className: "la-card" },
            h(
              CardContent,
              null,
              h("p", { className: "la-muted" }, "No Hermes profiles found on this host."),
              h(
                "p",
                { className: "la-note" },
                "The bridge will answer as the gateway default agent until a profile exists."
              )
            )
          )
        : h(
            React.Fragment,
            null,
            profiles.length > 8
              ? h(Input, {
                  className: "la-input la-filter",
                  placeholder: "Filter profiles…",
                  value: query,
                  onChange: function (e) {
                    setQuery(e.target.value);
                  },
                })
              : null,
            h(
              "div",
              { className: "la-grid" },
              profiles.indexOf("") === -1
                ? h(ProfileCard, {
                    key: "__default__",
                    name: "",
                    selected: !picked,
                    disabled: isSaving,
                    onSelect: setPicked,
                  })
                : null,
              visible.map(function (name) {
                return h(ProfileCard, {
                  key: name,
                  name: name,
                  selected: picked === name,
                  disabled: isSaving,
                  onSelect: setPicked,
                });
              })
            ),
            visible.length === 0
              ? h("p", { className: "la-muted" }, "No profile matches “" + query + "”.")
              : null,
            h(
              "div",
              { className: "la-actions" },
              h(
                Button,
                {
                  type: "button",
                  className: "la-button la-button-primary",
                  disabled: isSaving || !dirty,
                  onClick: save,
                },
                isSaving ? h("span", { className: "la-spinner", "aria-hidden": "true" }) : null,
                isSaving ? "Saving…" : dirty ? "Save agent" : "Saved"
              ),
              dirty
                ? h(
                    Button,
                    {
                      type: "button",
                      className: "la-button la-button-quiet",
                      disabled: isSaving,
                      onClick: function () {
                        setPicked(active || "");
                      },
                    },
                    "Reset"
                  )
                : null
            )
          ),
      message
        ? h(
            "p",
            {
              className: "la-toast" + (message.indexOf("Could not") === 0 ? " la-toast-bad" : ""),
              role: "status",
            },
            message
          )
        : null,
      h(
        "p",
        { className: "la-note" },
        "You can also label an issue with a profile name — ",
        profiles.filter(Boolean).slice(0, 4).join(", ") || "cody, appy",
        " — to route just that issue."
      )
    );
  }

  window.__HERMES_PLUGINS__.register("linear-agent", AgentPage);
})();
