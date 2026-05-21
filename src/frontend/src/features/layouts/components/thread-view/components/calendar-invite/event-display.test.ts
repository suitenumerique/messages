import { cleanEventForDisplay, extractUrl } from "./event-display";

describe("cleanEventForDisplay", () => {
    it("trims whitespace on all fields", () => {
        const r = cleanEventForDisplay({
            description: "  hi  ",
            location: "  there  ",
            url: "  https://x  ",
        });
        expect(r).toEqual({ description: "hi", location: "there", url: "https://x" });
    });

    it("strips the known French visioconférence prefix from location", () => {
        const r = cleanEventForDisplay({
            description: "",
            location:
                "Pour participer à la visioconférence, cliquez sur ce lien : https://visio.example.org/abc",
            url: "",
        });
        expect(r.location).toBe("https://visio.example.org/abc");
    });

    it("extracts the Google conference block from description to url", () => {
        const description =
            "Project sync\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~::~:~::-\nJoin with Google Meet: https://meet.google.com/abc-defg-hij\nLearn more at https://support.google.com/\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~::~:~::-";
        const r = cleanEventForDisplay({ description, location: "", url: "" });
        expect(r.url).toBe("https://meet.google.com/abc-defg-hij");
        expect(r.description).toBe("Project sync");
    });

    it("ignores conference URL with non-allowlisted host (anti-phishing)", () => {
        // Lookalike domain — must NOT be surfaced as a clickable conference URL.
        const description =
            "Project sync\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~::~:~::-\nJoin: https://meet-googIe.com/abc-defg-hij\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~::~:~::-";
        const r = cleanEventForDisplay({ description, location: "", url: "" });
        expect(r.url).toBe("");
        // Description left intact so the user can still read what's in there.
        expect(r.description).toBe(description);
    });

    it("rejects a non-allowlisted host in the conference block", () => {
        // The allowlist is intentionally Google-only for now, so a URL in
        // a ~:~ block on any other host (here zoom.us) must NOT be lifted
        // into the conference slot — otherwise a hand-crafted phishing
        // description could occupy the "videocam" row.
        const description =
            "-::~:~::~:~:~:~:~:~:~:~:~:~:~:~::~:~::-\nJoin: https://company.zoom.us/j/12345\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~::~:~::-";
        const r = cleanEventForDisplay({ description, location: "", url: "" });
        expect(r.url).toBe("");
        // The block stays in the description (we couldn't safely extract).
        expect(r.description).toBe(description);
    });

    it("keeps existing url and does not re-extract from description", () => {
        const description =
            "-::~:~::~:~:~:~:~:~:~:~:~:~:~:~::~:~::-\nJoin with Google Meet: https://meet.google.com/aaa-bbbb-ccc\nMore https://x\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~::~:~::-";
        const r = cleanEventForDisplay({
            description,
            location: "",
            url: "https://already-set.example",
        });
        expect(r.url).toBe("https://already-set.example");
        // description untouched when url was already present
        expect(r.description).toBe(description);
    });

    it("deduplicates description == location", () => {
        const r = cleanEventForDisplay({
            description: "same",
            location: "same",
            url: "",
        });
        expect(r.description).toBe("");
        expect(r.location).toBe("same");
    });

    it("deduplicates location == url", () => {
        const r = cleanEventForDisplay({
            description: "",
            location: "https://x",
            url: "https://x",
        });
        expect(r.location).toBe("");
        expect(r.url).toBe("https://x");
    });

    it("deduplicates description == url", () => {
        const r = cleanEventForDisplay({
            description: "https://x",
            location: "",
            url: "https://x",
        });
        expect(r.description).toBe("");
    });

    it("clears location when the extracted conference url matches it", () => {
        const description =
            "-::~:~::~:~:~:~:~:~:~:~:~:~:~:~::~:~::-\nJoin: https://meet.google.com/xyz\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~::~:~::-";
        const r = cleanEventForDisplay({
            description,
            location: "https://meet.google.com/xyz",
            url: "",
        });
        expect(r.url).toBe("https://meet.google.com/xyz");
        expect(r.location).toBe("");
        expect(r.description).toBe("");
    });

    it("leaves plain events untouched", () => {
        const r = cleanEventForDisplay({
            description: "Agenda",
            location: "Room 42",
            url: "",
        });
        expect(r).toEqual({ description: "Agenda", location: "Room 42", url: "" });
    });
});

describe("extractUrl", () => {
    it("returns the first url in a string", () => {
        expect(extractUrl("foo https://a.example bar https://b.example")).toBe(
            "https://a.example",
        );
    });

    it("returns null if no url", () => {
        expect(extractUrl("nothing here")).toBeNull();
    });
});
