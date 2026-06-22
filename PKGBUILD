# Maintainer: Roberth <roberth.halk@gmail.com>

pkgname=wallfliper
pkgver=1.0.0
pkgrel=1
pkgdesc="Minimalist, Rofi/yazi-style wallpaper selector for Wayland (wlr-layer-shell)"
arch=('any')
url="https://github.com/Roberth-Souza/wallfliper"
license=('GPL-3.0-or-later')

depends=(
  'python'
  'pyside6'
  'layer-shell-qt'
  'swww'
  'mpvpaper'
  'ffmpeg'
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
