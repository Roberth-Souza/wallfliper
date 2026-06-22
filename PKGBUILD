# Maintainer: Roberth <roberth.halk@gmail.com>
#
# AUR PKGBUILD draft. Not submittable yet — it needs a published GitHub repo and a
# tagged release (v1.0.0) so the `source` tarball below actually resolves.
# Before submitting:
#   1. Push the repo to github.com/Roberth-Souza/wallfliper and tag v1.0.0.
#   2. Run `updpkgsums` to fill the real sha256, then `makepkg --printsrcinfo > .SRCINFO`.
#   3. Test locally with `makepkg -si` before pushing to the AUR.

pkgname=wallfliper
pkgver=1.0.0
pkgrel=1
pkgdesc="Minimalist, Rofi/yazi-style wallpaper selector for Wayland (wlr-layer-shell)"
arch=('any')
url="https://github.com/Roberth-Souza/wallfliper"
license=('GPL-3.0-or-later')

# Hard requirements — without these the app cannot start.
depends=(
  'python'
  'pyside6'
  'layer-shell-qt'
)

# Wallpaper backends + media tooling. They're optdepends because the app runs and
# degrades gracefully without them (it tells you in the status bar what's missing).
# If you'd rather `paru -S wallfliper` pull a fully-loaded setup in one shot, move
# 'swww', 'mpvpaper', and 'ffmpeg' up into depends=() instead.
optdepends=(
  'swww: image wallpapers (or its fork awww)'
  'mpvpaper: video wallpapers'
  'ffmpeg: video thumbnails, previews, and color-frame extraction'
)

makedepends=(
  'python-build'
  'python-installer'
  'python-wheel'
  'python-setuptools'
)

source=("$pkgname-$pkgver.tar.gz::$url/archive/refs/tags/v$pkgver.tar.gz")
sha256sums=('SKIP')  # run `updpkgsums` once the release tag exists

build() {
  cd "$srcdir/$pkgname-$pkgver"
  python -m build --wheel --no-isolation
}

package() {
  cd "$srcdir/$pkgname-$pkgver"
  python -m installer --destdir="$pkgdir" dist/*.whl
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
