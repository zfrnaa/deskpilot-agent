package runner

import "testing"

func TestQuoteWindowsArg(t *testing.T) {
	tests := []struct {
		in   string
		want string
	}{
		{`simple`, `"simple"`},
		{`C:\Users\Test Dir\`, `"C:\Users\Test Dir\\"`},
		{`a"b`, `"a\"b"`},
		{`a\"b`, `"a\\\"b"`},
		{`--clear-ram`, `"--clear-ram"`},
	}

	for _, tc := range tests {
		got := quoteWindowsArg(tc.in)
		if got != tc.want {
			t.Errorf("quoteWindowsArg(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}
