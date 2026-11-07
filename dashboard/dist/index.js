/**
 * Linear agent — dashboard plugin
 *
 * One job: choose which Hermes agent answers Linear issues.
 *
 * Built strictly against the SDK surface exposed by web/src/plugins/registry.ts:
 *   - SDK.fetchJSON for /api/plugins/linear-agent/*  (never bare fetch: the
 *     dashboard sits behind gated auth + loopback tokens and fetchJSON carries them)
 *   - SDK.hooks.useToast -> { showToast, toast }, rendered with SDK.components.Toast
 *   - SDK.utils.cn for conditional classes
 *   - SDK.sdkVersion, checked and degraded gracefully
 *
 * Plain IIFE, no JSX, no bundler, no bundled React.
 */
(function () {
  "use strict";

  var registry = window.__HERMES_PLUGINS__;
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!registry || typeof registry.register !== "function" || !SDK || !SDK.React) {
    return;
  }

  var React = SDK.React;
  var h = React.createElement;

  var hooks = SDK.hooks || {};
  var useState = hooks.useState || React.useState;
  var useEffect = hooks.useEffect || React.useEffect;
  var useCallback = hooks.useCallback || React.useCallback;
  var useMemo = hooks.useMemo || React.useMemo;

  // Constant per page load, so calling it unconditionally keeps hook order stable
  // even on a host that predates useToast.
  var useToastSafe =
    typeof hooks.useToast === "function"
      ? hooks.useToast
      : function () {
          return { showToast: null, toast: null };
        };

  var C = SDK.components || {};
  var utils = SDK.utils || {};
  var cn =
    typeof utils.cn === "function"
      ? utils.cn
      : function () {
          return Array.prototype.slice.call(arguments).filter(Boolean).join(" ");
        };

  var Card = C.Card || fallback("div", "la-card");
  var CardContent = C.CardContent || fallback("div", "la-card-content");
  var Button = C.Button || fallback("button", "la-button");
  var Input = C.Input || fallback("input", "la-input");
  var Toast = C.Toast || null;

  var API = "/api/plugins/linear-agent";
  var SUPPORTED_SDK_MAJOR = 1;

  function fallback(tag, baseClass) {
    return function Fallback(props) {
      var rest = Object.assign({}, props);
      var children = rest.children;
      rest.className = cn(baseClass, rest.className);
      delete rest.children;
      return h(tag, rest, children);
    };
  }

  function sdkMajor(version) {
    var n = parseInt(String(version || "0").split(".")[0], 10);
    return isNaN(n) ? 0 : n;
  }

  function initials(name) {
    if (!name) return "?";
    var parts = String(name)
      .replace(/[^a-zA-Z0-9 _-]/g, " ")
      .trim()
      .split(/[\s_-]+/)
      .filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }

  function activeLabel(active) {
    return active || "the gateway default agent";
  }

  function messageOf(err) {
    if (!err) return "Unknown error";
    if (typeof err === "string") return err;
    // SDK.fetchJSON throws ApiError with a user-facing sentence in .message
    return String(err.message || err.detail || err);
  }

  function StatusPill(props) {
    var bridge = props.bridge || {};
    if (bridge.reachable) {
      return h(
        "span",
        { className: "la-pill la-pill-ok", title: bridge.url || "" },
        h("span", { className: "la-dot la-dot-ok" }),
        "bridge reachable" + (bridge.version ? " · v" + bridge.version : "")
      );
    }
    return h(
      "span",
      { className: "la-pill la-pill-warn", title: bridge.error || "" },
      h("span", { className: "la-dot la-dot-warn" }),
      "bridge unreachable"
    );
  }

  function ProfileCard(props) {
    return h(
      "button",
      {
        type: "button",
        className: cn("la-profile", props.selected && "la-profile-selected"),
        "aria-pressed": props.selected ? "true" : "false",
        disabled: props.disabled,
        onClick: function () {
          props.onSelect(props.name);
        },
      },
      h("span", { className: "la-avatar" }, initials(props.name)),
      h(
        "span",
        { className: "la-profile-text" },
        h("span", { className: "la-profile-name" }, props.name || "default"),
        h("span", { className: "la-profile-sub" }, props.name ? "profile" : "gateway default")
      ),
      props.selected ? h("span", { className: "la-check", "aria-hidden": "true" }, "✓") : null
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
        h(Button, { type: "button", onClick: props.onRetry }, "Retry")
      )
    );
  }

  function AgentPage() {
    var state = useState({ loading: true, error: "", data: null });
    var current = state[0];
    var setState = state[1];

    var pick = useState("");
    var picked = pick[0];
    var setPicked = pick[1];

    var saving = useState(false);
    var isSaving = saving[0];
    var setSaving = saving[1];

    var filter = useState("");
    var query = filter[0];
    var setQuery = filter[1];

    // SDK toast when the host provides one; the inline notice is the fallback.
    var toastState = useToastSafe();
    var notice = useState(null);
    var inlineNotice = notice[0];
    var setInlineNotice = notice[1];

    function notify(message, type) {
      if (toastState && typeof toastState.showToast === "function") {
        toastState.showToast(message, type);
      } else {
        setInlineNotice({ message: message, type: type });
      }
    }

    var load = useCallback(
      function (isRetry) {
        setState(function (prev) {
          return { loading: true, error: "", data: isRetry ? prev.data : null };
        });
        if (typeof SDK.fetchJSON !== "function") {
          setState({
            loading: false,
            error:
              "This dashboard does not expose SDK.fetchJSON, so the plugin cannot authenticate its API calls. Update Hermes.",
            data: null,
          });
          return;
        }
        Promise.resolve(SDK.fetchJSON(API + "/state"))
          .then(function (data) {
            setState({ loading: false, error: "", data: data });
            setPicked((data && data.active) || "");
          })
          .catch(function (err) {
            setState({ loading: false, error: messageOf(err), data: null });
          });
      },
      []
    );

    useEffect(
      function () {
        load(false);
      },
      [load]
    );

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
      setInlineNotice(null);
      Promise.resolve(
        SDK.fetchJSON(API + "/agent", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile: picked }),
        })
      )
        .then(function (next) {
          setState({ loading: false, error: "", data: next });
          setPicked((next && next.active) || "");
          setSaving(false);
          notify("Saved. Takes effect on the next Linear issue — no restart needed.", "success");
        })
        .catch(function (err) {
          setSaving(false);
          notify("Could not save: " + messageOf(err), "error");
        });
    }

    var dirty = (picked || "") !== (active || "");
    var sdkWarning =
      sdkMajor(SDK.sdkVersion) > SUPPORTED_SDK_MAJOR
        ? "This tab was built for plugin SDK v" +
          SUPPORTED_SDK_MAJOR +
          " and the dashboard reports v" +
          SDK.sdkVersion +
          "; behaviour may differ."
        : "";

    var toastEl =
      Toast && toastState && toastState.toast ? h(Toast, { toast: toastState.toast }) : null;
    var inlineEl =
      !toastEl && inlineNotice
        ? h(
            "p",
            {
              className: cn("la-toast", inlineNotice.type === "error" && "la-toast-bad"),
              role: "status",
            },
            inlineNotice.message
          )
        : null;

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
      sdkWarning ? h("p", { className: "la-note la-note-warn" }, sdkWarning) : null,
      h(
        "div",
        { className: "la-head" },
        h("h1", { className: "la-title" }, "Linear agent"),
        h(StatusPill, { bridge: data.bridge })
      ),
      h(
        "p",
        { className: "la-lede" },
        "Issues are answered by ",
        h("strong", { className: "la-accent" }, activeLabel(active)),
        "."
      ),
      h(
        "p",
        { className: "la-value" },
        "agent_profile: ",
        h("code", null, active || "(unset — gateway default)"),
        data.settings_path ? h("span", { className: "la-value-path" }, "  ·  " + data.settings_path) : null
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
                "The bridge answers as the gateway default agent until a profile exists."
              )
            )
          )
        : h(
            React.Fragment,
            null,
            profiles.length > 8
              ? h(Input, {
                  className: cn("la-filter"),
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
              h(ProfileCard, {
                key: "__default__",
                name: "",
                selected: !picked,
                disabled: isSaving,
                onSelect: setPicked,
              }),
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
              ? h("p", { className: "la-muted" }, 'No profile matches "' + query + '"')
              : null,
            h(
              "div",
              { className: "la-actions" },
              h(
                Button,
                {
                  type: "button",
                  className: cn("la-button-primary"),
                  disabled: isSaving || !dirty,
                  onClick: save,
                },
                isSaving ? h("span", { className: "la-spinner", "aria-hidden": "true" }) : null,
                isSaving ? "Saving…" : "Save agent"
              ),
              dirty
                ? h(
                    Button,
                    {
                      type: "button",
                      className: cn("la-button-quiet"),
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
      inlineEl,
      h(
        "p",
        { className: "la-note" },
        "You can also label an issue with a profile name — ",
        profiles.filter(Boolean).slice(0, 4).join(", ") || "cody, appy",
        " — to route just that issue."
      ),
      toastEl
    );
  }

  registry.register("linear-agent", AgentPage);
})();
