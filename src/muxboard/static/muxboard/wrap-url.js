/* Reconstruct http(s) URLs the Session hard-wrapped at the pane width.
 *
 * @xterm/addon-web-links only concatenates rows with isWrapped=true (xterm
 * soft wrap). A TUI that inserts a newline at cols (Claude login URLs) has
 * isWrapped=false on every row, so the Link is the first line and Copy keeps
 * the newlines. This module joins both kinds of wrap.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.mbWrapUrl = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Same default regex as @xterm/addon-web-links@0.11.0.
  var URL_REGEX =
    /(https?|HTTPS?):[/]{2}[^\s"'!*(){}|\\\^<>`]*[^\s"':,.!?{}|\\\^~\[\]`()<>]/;

  function isUrl(urlString) {
    try {
      var url = new URL(urlString);
      if (url.protocol !== "http:" && url.protocol !== "https:") return false;
      var base = url.protocol + "//" + url.host;
      return urlString.toLocaleLowerCase().indexOf(base.toLocaleLowerCase()) === 0;
    } catch (e) {
      return false;
    }
  }

  function hardContinues(prev, curr, cols) {
    var width = prev && prev.cols != null ? prev.cols : cols;
    return (
      !!prev &&
      !!curr &&
      width > 0 &&
      prev.text.length === width &&
      curr.text.length > 0 &&
      curr.text.indexOf(" ") === -1
    );
  }

  function windowAround(lines, index, cols) {
    if (!lines.length || index < 0 || index >= lines.length) {
      return { start: index, texts: [] };
    }
    var top = index;
    while (top > 0) {
      var curr = lines[top];
      var prev = lines[top - 1];
      if ((curr && curr.isWrapped) || hardContinues(prev, curr, cols)) {
        top--;
      } else {
        break;
      }
    }
    var bottom = index;
    while (bottom + 1 < lines.length) {
      var next = lines[bottom + 1];
      var here = lines[bottom];
      if ((next && next.isWrapped) || hardContinues(here, next, cols)) {
        bottom++;
      } else {
        break;
      }
    }
    var texts = [];
    for (var i = top; i <= bottom; i++) {
      texts.push(lines[i] ? lines[i].text : "");
    }
    return { start: top, texts: texts };
  }

  function mapIndex(texts, startLine, pos) {
    var line = startLine;
    var remain = pos;
    for (var i = 0; i < texts.length; i++) {
      var t = texts[i];
      if (remain < t.length) return { x: remain + 1, y: line + 1 };
      remain -= t.length;
      line++;
    }
    var last = texts[texts.length - 1] || "";
    return { x: last.length, y: startLine + texts.length };
  }

  function linksOnLine(lines, index, cols, activate) {
    var win = windowAround(lines, index, cols);
    if (!win.texts.length) return [];
    var joined = win.texts.join("");
    var rex = new RegExp(URL_REGEX.source, (URL_REGEX.flags || "") + "g");
    var out = [];
    var match;
    while ((match = rex.exec(joined))) {
      var text = match[0];
      if (!isUrl(text)) continue;
      var start = mapIndex(win.texts, win.start, match.index);
      var end = mapIndex(win.texts, win.start, match.index + text.length - 1);
      out.push({
        range: { start: start, end: end },
        text: text,
        activate: activate,
      });
    }
    return out;
  }

  function copyText(selection) {
    if (!selection) return selection;
    if (selection.indexOf("\n") === -1 && selection.indexOf("\r") === -1) {
      return selection;
    }
    var joined = selection
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .split("\n")
      .join("");
    if (/\s/.test(joined)) return selection;
    var rex = new RegExp(URL_REGEX.source);
    var m = rex.exec(joined);
    if (m && m[0] === joined && isUrl(joined)) return joined;
    return selection;
  }

  function bufferLines(term) {
    var buf = term.buffer.active;
    var rows = [];
    for (var i = 0; i < buf.length; i++) {
      var line = buf.getLine(i);
      rows.push(
        line
          ? {
              text: line.translateToString(true),
              isWrapped: !!line.isWrapped,
              cols: line.length,
            }
          : { text: "", isWrapped: false, cols: 0 }
      );
    }
    return rows;
  }

  return {
    URL_REGEX: URL_REGEX,
    isUrl: isUrl,
    windowAround: windowAround,
    linksOnLine: linksOnLine,
    copyText: copyText,
    bufferLines: bufferLines,
  };
});
