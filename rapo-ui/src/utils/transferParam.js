// The text of a .param file as lines, and back. A line is a comment, a blank or a Name:value parameter; comments and
// the order of the lines stay as written when the parameters are changed from the table of the editor.

// Lines of a param text: {name, value} for a parameter, {raw} for anything else (comments, blanks, text that is no
// Name:value, which the server refuses on saving).
export function parseParam(text) {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/\n$/, "")
    .split("\n")
    .filter((line, index, all) => all.length > 1 || line !== "")
    .map((line) => {
      const trimmed = line.trim();
      const at = trimmed.indexOf(":");
      if (!trimmed || trimmed.startsWith("#") || at < 1) {
        return { raw: line };
      }
      // Values may hold ':' (a Windows path, a time), so only the first one splits.
      return { name: trimmed.slice(0, at).trim(), value: trimmed.slice(at + 1).trim() };
    });
}

// The parameters of the lines that have a value, in the order written; a name written twice keeps its last value, as
// the server reads it.
export function itemsOf(lines) {
  const items = new Map();
  lines
    .filter((line) => line.name !== undefined && line.value !== "")
    .forEach((line) => {
      items.delete(line.name);
      items.set(line.name, line.value);
    });
  return [...items].map(([name, value]) => ({ name, value }));
}

// The text for the shown parameters ({name, value}, an empty value is not written): the lines keep their place and
// comments, a parameter that is not shown any more is dropped, a new one is added at the end.
export function applyParam(lines, items) {
  const wanted = new Map(items.filter((item) => item.value !== "" && item.value != null).map((item) => [item.name, String(item.value).trim()]));
  const written = new Set();
  const out = [];
  lines.forEach((line) => {
    if (line.name === undefined) {
      out.push(line.raw);
    } else if (wanted.has(line.name) && !written.has(line.name)) {
      written.add(line.name);
      out.push(`${line.name}:${wanted.get(line.name)}`);
    }
  });
  items.forEach((item) => {
    if (wanted.has(item.name) && !written.has(item.name)) {
      written.add(item.name);
      out.push(`${item.name}:${wanted.get(item.name)}`);
    }
  });
  while (out.length && out[out.length - 1].trim() === "") {
    out.pop();
  }
  return out.length ? out.join("\n") + "\n" : "";
}
