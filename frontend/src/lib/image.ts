/** 上传前把图片压缩到最长边 ≤ 1024px、转 JPEG quality 0.85（M6 交互要求）。 */

const DEFAULT_MAX_EDGE = 1024
const DEFAULT_QUALITY = 0.85

function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file)
    const image = new Image()
    image.onload = () => {
      URL.revokeObjectURL(objectUrl)
      resolve(image)
    }
    image.onerror = () => {
      URL.revokeObjectURL(objectUrl)
      reject(new Error('图片读取失败，请换一张图片'))
    }
    image.src = objectUrl
  })
}

/**
 * 返回压缩后的 File（始终 .jpg）。非图片或压缩失败时原样返回，
 * 避免因为压缩把上传流程卡死。
 */
export async function compressImage(
  file: File,
  maxEdge: number = DEFAULT_MAX_EDGE,
  quality: number = DEFAULT_QUALITY,
): Promise<File> {
  if (!file.type.startsWith('image/')) return file
  try {
    const image = await loadImage(file)
    const scale = Math.min(1, maxEdge / Math.max(image.width, image.height))
    const width = Math.max(1, Math.round(image.width * scale))
    const height = Math.max(1, Math.round(image.height * scale))

    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (!context) return file
    context.drawImage(image, 0, 0, width, height)

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', quality),
    )
    if (!blob) return file
    const name = file.name.replace(/\.[^.]+$/, '') + '.jpg'
    return new File([blob], name, { type: 'image/jpeg' })
  } catch {
    return file
  }
}

/** 读取图片尺寸，便于在界面上展示「压缩生效」的实测结果。 */
export async function readImageSize(
  file: File,
): Promise<{ width: number; height: number }> {
  const image = await loadImage(file)
  return { width: image.width, height: image.height }
}
