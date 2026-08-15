import { type NextRequest, NextResponse } from "next/server";

import { ApiError, getCompanySuggestions } from "@/lib/api";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const query = request.nextUrl.searchParams.get("q")?.trim() ?? "";
  if ([...query].length < 2 || [...query].length > 240) {
    return NextResponse.json([]);
  }
  try {
    return NextResponse.json(await getCompanySuggestions(query));
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 500;
    return NextResponse.json(
      { detail: status === 401 ? "authentication_required" : "suggestions_unavailable" },
      { status },
    );
  }
}
