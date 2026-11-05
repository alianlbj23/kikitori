//! Newline-delimited reader with a hard per-line limit.
//!
//! Role: sits between the engine's stdout/stderr pipes and the protocol parser.
//! `AsyncBufReadExt::lines` would buffer an unbounded line in memory; a broken
//! engine printing megabytes without a newline must not be able to do that, so
//! over-long lines are discarded (and reported) while still being consumed up to
//! their newline so the stream stays in sync.

use tokio::io::{AsyncBufRead, AsyncBufReadExt};

/// Result of reading one line.
#[derive(Debug, PartialEq, Eq)]
pub enum LineRead {
    /// A complete line without its `\n` / `\r\n` terminator.
    Line(Vec<u8>),
    /// A line longer than the limit; its bytes were dropped.
    TooLong { length: usize },
    /// End of stream (a trailing line without newline is returned first).
    Eof,
}

/// Reads lines of at most `max_len` bytes from an [`AsyncBufRead`].
pub struct BoundedLineReader<R> {
    inner: R,
    max_len: usize,
    buf: Vec<u8>,
    /// Length of the current line when it has already exceeded `max_len`.
    overflow: Option<usize>,
}

impl<R: AsyncBufRead + Unpin> BoundedLineReader<R> {
    pub fn new(inner: R, max_len: usize) -> Self {
        Self {
            inner,
            max_len,
            buf: Vec::new(),
            overflow: None,
        }
    }

    /// Reads the next line.
    pub async fn next_line(&mut self) -> std::io::Result<LineRead> {
        loop {
            let available = self.inner.fill_buf().await?;
            if available.is_empty() {
                // EOF: flush whatever partial line we hold.
                if let Some(length) = self.overflow.take() {
                    return Ok(LineRead::TooLong { length });
                }
                if self.buf.is_empty() {
                    return Ok(LineRead::Eof);
                }
                return Ok(LineRead::Line(self.take_line()));
            }
            let newline = available.iter().position(|&b| b == b'\n');
            let chunk_len = newline.unwrap_or(available.len());
            append(
                &mut self.buf,
                &mut self.overflow,
                self.max_len,
                &available[..chunk_len],
            );
            let consumed = newline.map_or(chunk_len, |p| p + 1);
            self.inner.consume(consumed);
            if newline.is_some() {
                if let Some(length) = self.overflow.take() {
                    return Ok(LineRead::TooLong { length });
                }
                return Ok(LineRead::Line(self.take_line()));
            }
        }
    }

    fn take_line(&mut self) -> Vec<u8> {
        let mut line = std::mem::take(&mut self.buf);
        if line.last() == Some(&b'\r') {
            line.pop();
        }
        line
    }
}

/// Free function so it can borrow the buffer fields while `inner` is borrowed
/// by `fill_buf`.
fn append(buf: &mut Vec<u8>, overflow: &mut Option<usize>, max_len: usize, chunk: &[u8]) {
    if let Some(length) = overflow.as_mut() {
        *length += chunk.len();
        return;
    }
    if buf.len() + chunk.len() > max_len {
        *overflow = Some(buf.len() + chunk.len());
        // Release the memory now rather than holding up to max_len per pipe.
        *buf = Vec::new();
    } else {
        buf.extend_from_slice(chunk);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::BufReader;

    async fn read_all(input: &[u8], max: usize, buf_cap: usize) -> Vec<LineRead> {
        let mut reader = BoundedLineReader::new(BufReader::with_capacity(buf_cap, input), max);
        let mut out = Vec::new();
        loop {
            let item = reader.next_line().await.unwrap();
            let eof = item == LineRead::Eof;
            out.push(item);
            if eof {
                return out;
            }
        }
    }

    #[tokio::test]
    async fn splits_lines_and_strips_crlf() {
        for cap in [1, 3, 64] {
            let got = read_all(b"a\r\nbc\n\nlast", 100, cap).await;
            assert_eq!(
                got,
                vec![
                    LineRead::Line(b"a".to_vec()),
                    LineRead::Line(b"bc".to_vec()),
                    LineRead::Line(Vec::new()),
                    LineRead::Line(b"last".to_vec()),
                    LineRead::Eof,
                ],
                "buffer capacity {cap}"
            );
        }
    }

    #[tokio::test]
    async fn drops_over_long_lines_and_resyncs() {
        for cap in [1, 4, 64] {
            let got = read_all(b"short\n0123456789\nok\n", 5, cap).await;
            assert_eq!(
                got,
                vec![
                    LineRead::Line(b"short".to_vec()),
                    LineRead::TooLong { length: 10 },
                    LineRead::Line(b"ok".to_vec()),
                    LineRead::Eof,
                ],
                "buffer capacity {cap}"
            );
        }
    }

    #[tokio::test]
    async fn over_long_trailing_line_at_eof() {
        let got = read_all(b"0123456789", 5, 4).await;
        assert_eq!(got, vec![LineRead::TooLong { length: 10 }, LineRead::Eof]);
    }
}
