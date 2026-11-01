import { createFileRoute } from "@tanstack/react-router";
import LandingPage from "../../Landing_page/src/LandingPage";
import landingCss from "../../Landing_page/src/landing.css?url";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "ProoFix — Evidence before merge" },
      { name: "description", content: "Reproduce failures, constrain repairs, and challenge AI-generated changes. Inspect the evidence before deciding to merge." },
    ],
    links: [
      { rel: "stylesheet", href: landingCss },
      { rel: "stylesheet", href: "https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:wght@300;400;500;600;700&display=swap" },
    ],
  }),
  component: LandingPage,
});
