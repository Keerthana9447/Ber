import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

describe("rendered user-provided text", () => {
  it("escapes markup instead of rendering it as HTML", () => {
    const unsafeText = "<img src=x onerror=alert(1)>";
    const html = renderToStaticMarkup(<p>{unsafeText}</p>);

    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(html).not.toContain("<img");
  });
});
